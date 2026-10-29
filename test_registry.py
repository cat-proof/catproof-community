import json
import threading
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer
import app
from registry import JSONResponse, Registry, ConnectorError, databricks, servicenow, get_json

DB = {'DATABRICKS_HOST':'https://example.invalid','DATABRICKS_TOKEN':'test-secret'}
SN = {'SERVICENOW_INSTANCE_URL':'https://example.invalid','SERVICENOW_TOKEN':'test-secret'}

class RegistryTests(unittest.TestCase):
    def test_seeded_and_stable_ids(self):
        r=Registry({}); a=r.refresh(); b=r.refresh()
        self.assertEqual(len(a['agents']),6)
        self.assertTrue(all(x['provenance']=='seeded' for x in a['agents']))
        self.assertEqual([x['id'] for x in a['agents']],[x['id'] for x in b['agents']])

    def test_databricks_pages_and_unknown_supervisor(self):
        calls=[]
        def fetch(host,path,token,params):
            calls.append((path,params))
            if path.endswith('supervisor-agents'):
                return {'supervisor_agents':[{'id':'a','display_name':'Supervisor'}]}
            if 'page_token' not in params:
                return {'knowledge_assistants':[{'id':'a','state':'ONLINE','creator':'owner'}],'next_page_token':'page2'}
            return {'knowledge_assistants':[{'id':'b','state':'CREATING'}]}
        rows=databricks(DB,fetch)
        self.assertEqual(len(rows),3)
        self.assertEqual(len({x['id'] for x in rows}),3)
        self.assertEqual(rows[-1]['status'],'unknown')
        self.assertEqual(rows[0]['owner'],'owner')
        self.assertEqual(rows[0]['evidence']['creator'],'owner')
        self.assertIn('Databricks creator',rows[0]['evidence']['owner_type'])
        self.assertEqual(rows[-1]['owner'],'Unassigned')
        self.assertEqual(calls[1][1]['page_token'],'page2')

    def test_servicenow_display_fields_and_pages(self):
        calls=[]
        def fetch(host,path,token,params):
            calls.append(params)
            offset=params['sysparm_offset']
            return JSONResponse({'result':[{'sys_id':str(i),'name':{'value':'Agent'},'active':{'value':'false','display_value':'false'},'managed_by':{'value':'id','display_value':'Owner'}} for i in range(100)] if offset==0 else []}, {'Link':'<https://example.invalid/api/now/table/sn_aia_agent?sysparm_offset=100&sysparm_limit=100>;rel="next"'} if offset==0 else {})
        rows=servicenow(dict(SN,SERVICENOW_OWNER_FIELD='managed_by'),fetch)
        self.assertEqual(len(rows),100)
        self.assertEqual(rows[0]['owner'],'Owner')
        self.assertEqual(rows[0]['status'],'inactive')
        self.assertEqual(calls[1]['sysparm_offset'],100)

    def test_failure_retains_last_seen_and_other_source(self):
        def fetch(host,path,token,params):
            return {path.split('/')[-1].replace('-','_'):[{'id':'a','state':'ONLINE'}]}
        r=Registry(DB,fetch); before=r.refresh()
        def fail(*args): raise ConnectorError('Read failed (HTTP 403).')
        r.fetch=fail; after=r.refresh()
        old=[x for x in before['agents'] if x['source']=='Databricks']
        new=[x for x in after['agents'] if x['source']=='Databricks']
        self.assertEqual([x['last_seen'] for x in old],[x['last_seen'] for x in new])
        self.assertTrue(all(x['stale'] and x['provenance']=='live' for x in new))
        self.assertEqual(after['sources'][1]['health'],'ok')
        self.assertNotIn('test-secret',json.dumps(after))

    def test_first_failure_never_substitutes_seeds(self):
        def fail(*args): raise ConnectorError('Unavailable')
        r=Registry(DB,fail); result=r.refresh()
        self.assertEqual(len(result['agents']),3)
        self.assertTrue(all(x['source']=='ServiceNow' for x in result['agents']))
        self.assertEqual(result['sources'][0]['health'],'error')

    def test_incomplete_config_and_malformed_results(self):
        r=Registry({'DATABRICKS_TOKEN':'test-secret'})
        self.assertEqual(r.refresh()['sources'][0]['mode'],'configuration error')
        for payload in [{}, {'knowledge_assistants':[{}]}]:
            with self.assertRaises(ConnectorError): databricks(DB,lambda *args:payload)

    def test_repeated_page_token(self):
        with self.assertRaises(ConnectorError):
            databricks(DB,lambda *args:{'knowledge_assistants':[],'next_page_token':'same'})

    def test_https_origin_validation(self):
        for host in ['http://example.com','https://user:password@example.com','https://example.com/path']:
            with self.assertRaises(ConnectorError): get_json(host,'/x','secret',{})

    def test_successful_refresh_removes_absent_records(self):
        r=Registry(DB,lambda host,path,*args:{path.split('/')[-1].replace('-','_'):[{'id':'a'}]})
        r.refresh();r.fetch=lambda host,path,*args:{path.split('/')[-1].replace('-','_'):[]}
        self.assertEqual(len(r.refresh()['agents']),3)

class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.registry=Registry({});app.registry.refresh()
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.url='http://127.0.0.1:'+str(cls.server.server_port)
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join()
    def test_inventory_and_dashboard(self):
        with urlopen(self.url+'/api/inventory') as r:self.assertEqual(len(json.load(r)['agents']),6)
        with urlopen(self.url) as r:self.assertIn(b'Agent Atlas',r.read())
    def test_refresh_origin_guard(self):
        for headers in [{},{'Origin':'https://evil.invalid','X-Registry-Action':'refresh'}]:
            with self.assertRaises(HTTPError) as error:urlopen(Request(self.url+'/api/refresh',method='POST',headers=headers))
            self.assertEqual(error.exception.code,403)
            error.exception.close()
        with urlopen(Request(self.url+'/api/refresh',method='POST',headers={'X-Registry-Action':'refresh'})) as r:self.assertEqual(r.status,200)

if __name__=='__main__': unittest.main()
