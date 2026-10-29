import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request,build_opener,HTTPRedirectHandler
from urllib.error import HTTPError
from enterprise import RegistryAccounts as Accounts
from hosted_app import create_server
from hosted_store import HostedStore
from test_accounts import PostgreSQLConnection

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args):return None

class AccountHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();path=str(Path(self.tmp.name)/'db');connect=lambda *a,**kw:PostgreSQLConnection(path)
        self.persistence=HostedStore(path,connect);self.accounts=Accounts(path,connect)
        self.server=create_server({'APP_PASSWORD':'fixture-bootstrap-password','APP_ORIGINS':'https://registry.example,https://catproof.ai','APP_PUBLIC_ORIGIN':'https://registry.example'},self.persistence,('127.0.0.1',0),accounts=self.accounts)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.url='http://127.0.0.1:'+str(self.server.server_port)
    def tearDown(self):self.server.shutdown();self.server.server_close();self.thread.join();self.persistence.store.db.close();self.tmp.cleanup()
    def request(self,path,body=None,cookie='',host='registry.example',origin=None,form=False):
        headers={'Host':host,'Origin':origin or 'https://'+host,'X-Registry-Action':'refresh'}
        if cookie:headers['Cookie']=cookie
        data=(urlencode(body).encode() if form else json.dumps(body).encode()) if body is not None else None
        try:return build_opener(NoRedirect()).open(Request(self.url+path,data=data,headers=headers))
        except HTTPError as err:return err
    def signup(self,email):
        with self.request('/signup',{'name':'Fixture Owner','email':email,'workspace':'Fixture Team','password':'fixture-personal-password'},form=True) as response:
            self.assertEqual(response.code,303);self.assertEqual(response.headers['Location'],'/')
            self.assertIn('Secure',response.headers['Set-Cookie']);self.assertNotIn('Domain=',response.headers['Set-Cookie'])
            return response.headers['Set-Cookie'].split(';')[0]
    def test_signup_first_record_and_cross_account_access(self):
        a=self.signup('one@example.test');b=self.signup('two@example.test')
        with self.request('/api/agents',{'name':'Private owner one'},a) as response:
            data=json.load(response);self.assertEqual(response.code,201);eid=data['created_id']
        for path,key in [('/api/inventory','agents'),('/api/connections','sources'),('/api/history','events'),('/api/assets','assets')]:
            with self.request(path,cookie=b) as response:self.assertEqual(json.load(response)[key],[])
        with self.request('/api/agents/'+eid,cookie=b) as response:self.assertEqual(response.code,404)
        with self.request('/api/annotate',{'registry_id':eid,'updates':{'name':'Foreign modification'}},b) as response:self.assertEqual(response.code,404)
        with self.request('/api/export.csv',cookie=b) as response:self.assertNotIn(b'Private owner one',response.read())
        with self.request('/api/me',cookie=a) as response:
            identity=json.load(response);self.assertEqual(identity['email'],'one@example.test');self.assertFalse(identity['email_verified'])
            self.assertTrue(identity['credential_namespace'].startswith('CATPROOF_WS_'))
        with self.request('/api/inventory',cookie=a) as response:self.assertEqual(json.load(response)['agents'][0]['name'],'Private owner one')
    def test_login_and_existing_session_return_to_dashboard(self):
        self.signup('returning@example.test')
        with self.request('/login',{'username':'returning@example.test','password':'fixture-personal-password'},form=True) as response:
            self.assertEqual(response.code,303)
            self.assertEqual(response.headers['Location'],'/')
            cookie=response.headers['Set-Cookie'].split(';')[0]
        for path in ('/login','/signup'):
            with self.request(path,cookie=cookie) as response:
                self.assertEqual(response.code,303)
                self.assertEqual(response.headers['Location'],'/')
        with self.request('/',cookie=cookie) as response:
            self.assertEqual(response.code,200)
            self.assertIn(b'/brand-theme.css',response.read())
        with self.request('/login',{'username':'returning@example.test','password':'wrong'},form=True) as response:
            self.assertEqual(response.headers['Location'],'/login?error=credentials')
        with self.request('/') as response:self.assertEqual(response.headers['Location'],'https://registry.example/login')

    def test_logout_revokes_the_actual_cookie(self):
        cookie=self.signup('out@example.test')
        with self.request('/logout',{},cookie) as response:self.assertEqual(response.code,303)
        with self.request('/api/inventory',cookie=cookie) as response:self.assertEqual(response.code,401)
    def test_marketing_and_canonical_signup_host_and_exact_origin(self):
        with self.request('/',host='catproof.ai') as response:
            self.assertEqual(response.code,200);self.assertIn(b'https://registry.example/signup',response.read())
        with self.request('/signup',host='catproof.ai') as response:self.assertEqual(response.headers['Location'],'https://registry.example/signup')
        with self.request('/signup',{'name':'X'},host='registry.example',origin='https://catproof.ai',form=True) as response:self.assertEqual(response.code,403)
        with self.request('/signup') as response:self.assertIn(b'Create your registry',response.read())
    def test_existing_team_access_stays_separate(self):
        new=self.signup('new@example.test')
        with self.persistence.request(True,'legacy') as store:store.create({'name':'Existing team record'})
        with self.request('/login',{'username':'neeraj','password':'fixture-bootstrap-password'},form=True) as response:legacy=response.headers['Set-Cookie'].split(';')[0]
        with self.request('/api/inventory',cookie=legacy) as response:self.assertEqual(json.load(response)['agents'][0]['name'],'Existing team record')
        with self.request('/api/inventory',cookie=new) as response:self.assertEqual(json.load(response)['agents'],[])

    def test_duplicate_connection_opens_existing_without_overwrite_or_tenant_leak(self):
        first=self.signup('first@example.test');other=self.signup('other@example.test')
        body={'name':'My Lovable','kind':'lovable','url':'https://api.lovable.dev','credential_prefix':'LOVABLE','config':{'workspace_id':'workspace-fixture'}}
        with self.request('/api/connections',body,first) as r:self.assertEqual(r.code,201);cid=json.load(r)['created_connection_id']
        with self.request('/api/connections',{**body,'name':'Another label','credential_prefix':'OTHER'},first) as r:
            self.assertEqual(r.code,409);data=json.load(r);self.assertEqual(data['connection_id'],cid);self.assertIn('already saved',data['error'])
        with self.request('/api/inventory',cookie=first) as r:
            data=json.load(r);self.assertEqual(len(data['sources']),1);self.assertEqual(data['sources'][0]['credential_prefix'],'LOVABLE');self.assertFalse(data['sources'][0]['credential_configured']);self.assertIsNone(data['sources'][0]['last_success']);self.assertEqual(data['runs'],[])
        with self.request('/api/connections',body,other) as r:self.assertEqual(r.code,201);self.assertNotEqual(json.load(r)['created_connection_id'],cid)
