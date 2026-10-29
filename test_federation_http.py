from tests.db import registry as FederatedRegistry
import json
import tempfile
import threading
import unittest
from pathlib import Path
from http.server import ThreadingHTTPServer
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from federation_app import FederationHandler
from test_federation import record,payload

class FederationHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.servers=[];self.threads=[];self.stores=[]
        self.store,self.url=self.start('primary')
        self.peer,self.peer_url=self.start('peer')
    def start(self,name):
        store=FederatedRegistry(Path(self.tmp.name)/(name+'.db'),env={});server=ThreadingHTTPServer(('127.0.0.1',0),FederationHandler);store.local_port=server.server_port;server.store=store
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.servers.append(server);self.threads.append(thread);self.stores.append(store)
        return store,'http://127.0.0.1:'+str(server.server_port)
    def tearDown(self):
        for s in self.servers:s.shutdown();s.server_close()
        for t in self.threads:t.join()
        for s in self.stores:s.db.close()
        self.tmp.cleanup()
    def post(self,url,path,data,origin=None):
        headers={'X-Registry-Action':'refresh','Content-Type':'application/json'}
        if origin:headers['Origin']=origin
        return urlopen(Request(url+path,method='POST',headers=headers,data=json.dumps(data).encode()))
    def test_real_http_federation_and_local_canonical_authority(self):
        eid=self.peer.create({'name':'Peer test agent','business_owner':'Remote owner'})
        with self.post(self.url,'/api/connections',{'name':'Remote registry','kind':'atlas','url':self.peer_url}) as r:cid=json.load(r)['created_connection_id']
        with self.post(self.url,'/api/refresh',{'connection_id':cid}) as r:data=json.load(r)
        self.assertEqual(len(data['agents']),1);a=data['agents'][0]
        self.assertEqual(a['linked_records'][0]['source_id'],eid)
        self.assertEqual(a['business_owner'],'') # remote owner remains source evidence
        with self.post(self.url,'/api/annotate',{'registry_id':a['registry_id'],'updates':{'business_owner':'Local owner','lifecycle':'active'}}) as r:self.assertEqual(json.load(r)['agents'][0]['business_owner'],'Local owner')
        self.peer.update(eid,{'name':'Changed peer agent'})
        with self.post(self.url,'/api/refresh',{'connection_id':cid}) as r:data=json.load(r)
        self.assertEqual(data['agents'][0]['name'],'Peer test agent');self.assertEqual(data['agents'][0]['linked_records'][0]['name'],'Changed peer agent')
        with urlopen(self.url+'/api/catalog') as r:self.assertEqual(json.load(r)['schema_version'],'atlas.registry.v1')
    def test_manual_register_link_and_split_over_http(self):
        cid=self.store.add_connection({'name':'Source','kind':'atlas','url':self.peer_url});self.store.ingest(cid,payload(record()))
        oid=self.store.snapshot()['observations'][0]['observation_id']
        with self.post(self.url,'/api/agents',{'name':'Canonical business agent'}) as r:target=json.load(r)['created_id']
        with self.post(self.url,'/api/link',{'observation_id':oid,'registry_id':target}) as r:self.assertEqual(next(a for a in json.load(r)['agents'] if a['registry_id']==target)['linked_records'][0]['observation_id'],oid)
        with self.post(self.url,'/api/unlink',{'observation_id':oid}) as r:self.assertFalse(next(a for a in json.load(r)['agents'] if a['registry_id']==target)['linked_records'])
    def test_origin_guard_and_no_self_connection(self):
        with self.assertRaises(HTTPError) as err:self.post(self.url,'/api/agents',{'name':'x'},origin='https://evil.invalid')
        self.assertEqual(err.exception.code,403);err.exception.close()
        with self.assertRaises(HTTPError) as err:self.post(self.url,'/api/connections',{'name':'self','kind':'atlas','url':self.url})
        self.assertEqual(err.exception.code,400);err.exception.close()
    def test_setup_guides_and_offline_check(self):
        with urlopen(self.url+'/api/setup-guides') as response:
            guides=json.load(response);self.assertEqual(set(guides['guides']),{'snowflake','copilot','aws','a2a','google','lovable','credentials'})
        cid=self.store.add_connection({'name':'Snowflake','kind':'snowflake','url':'https://snowflake.invalid','credential_prefix':'SF','config':{'database':'DB','schema':'PUBLIC'}})
        with self.post(self.url,'/api/connection-check',{'connection_id':cid}) as response:self.assertFalse(json.load(response)['ready'])
        self.assertEqual(self.store.snapshot()['runs'],[]) # checking setup never calls the provider

    def test_local_demo_is_available_without_adding_seeded_registry_records(self):
        before=self.store.snapshot()
        for path in ('/demo','/demo/model.js','/demo/app.js','/demo/style.css'):
            with urlopen(self.url+path) as response:self.assertEqual(response.status,200)
        self.assertEqual(self.store.snapshot(),before)
