import json
import tempfile
import threading
import unittest
from pathlib import Path
from http.server import ThreadingHTTPServer
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from tests.db import product_store as ProductStore
from product_app import ProductHandler
from test_product import snapshot

class ProductHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=ProductStore(Path(self.tmp.name)/'db','test-workspace')
        self.initial=self.store.ingest(snapshot());self.server=ThreadingHTTPServer(('127.0.0.1',0),ProductHandler)
        self.server.store=self.store;self.server.discover=lambda refresh:snapshot()
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.url='http://127.0.0.1:'+str(self.server.server_port)
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.store.db.close();self.tmp.cleanup()
    def post(self,path,body,headers=None):
        return urlopen(Request(self.url+path,data=json.dumps(body).encode(),headers=headers or {'X-Registry-Action':'refresh'},method='POST'))
    def test_save_refresh_and_export_scope(self):
        rid=self.initial['agents'][0]['registry_id']
        with self.post('/api/annotate',{'registry_id':rid,'updates':{'business_owner':'Operations'}}) as r:
            self.assertTrue(any(a['business_owner']=='Operations' for a in json.load(r)['agents']))
        with self.post('/api/refresh',{}) as r:self.assertTrue(any(a['business_owner']=='Operations' for a in json.load(r)['agents']))
        with urlopen(self.url+'/api/export.csv') as r:self.assertEqual(len(r.read().decode('utf-8-sig').splitlines()),1)
        with urlopen(self.url+'/api/export.csv?seeded=1') as r:self.assertGreater(len(r.read().splitlines()),1)
    def test_cross_origin_and_invalid_annotation(self):
        with self.assertRaises(HTTPError) as error:
            self.post('/api/annotate',{}, {'Origin':'https://other.invalid','X-Registry-Action':'refresh'})
        self.assertEqual(error.exception.code,403);error.exception.close()
        with self.assertRaises(HTTPError) as error:
            self.post('/api/annotate',{'registry_id':self.initial['agents'][0]['registry_id'],'updates':{'status':'safe'}})
        self.assertEqual(error.exception.code,400);error.exception.close()
    def test_failed_upstream_keeps_saved_inventory(self):
        def fail(refresh):raise RuntimeError('private upstream failure')
        self.server.discover=fail
        with self.assertRaises(HTTPError) as error:self.post('/api/refresh',{})
        self.assertEqual(error.exception.code,502)
        self.assertNotIn(b'private',error.exception.read());error.exception.close()
        self.assertEqual(len(self.store.snapshot()['agents']),3)
