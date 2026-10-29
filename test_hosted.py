from tests.db import registry as FederatedRegistry
import contextlib
import json
import threading
import unittest
from urllib.request import Request,build_opener,HTTPRedirectHandler
from urllib.error import HTTPError
from hosted_app import create_server
from hosted_store import HostedStore
from enterprise import RegistryAccounts as Accounts
from test_accounts import PostgreSQLConnection
import tempfile
from pathlib import Path

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args):return None

class MemoryPersistence(HostedStore):
    def __init__(self):
        from tests.db import database
        url,connect=database();super().__init__(url,connect);self.fail=False
    @contextlib.contextmanager
    def request(self,write=False,workspace_id='legacy'):
        with super().request(write,workspace_id) as registry:
            yield registry
            if write and self.fail:raise RuntimeError('Fixture commit failure')
    def healthy(self):return not self.fail

class HostedHTTPTests(unittest.TestCase):
    def setUp(self):
        self.account_tmp=tempfile.TemporaryDirectory();self.account_path=str(Path(self.account_tmp.name)/'accounts.db');self.accounts=Accounts(self.account_path,lambda *a,**kw:PostgreSQLConnection(self.account_path))
        self.persistence=MemoryPersistence();self.server=create_server({'APP_PASSWORD':'fixture-strong-password','APP_ORIGIN':'https://registry.example','TEAM_USERNAMES':'neeraj,mark'},self.persistence,('127.0.0.1',0),accounts=self.accounts)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.url='http://127.0.0.1:'+str(self.server.server_port);self.cookie=None
    def tearDown(self):self.server.shutdown();self.server.server_close();self.thread.join();self.persistence.store.db.close();self.account_tmp.cleanup()
    def request(self,path,data=None,origin='https://registry.example',host='registry.example',cookie=True):
        headers={'Host':host,'Origin':origin,'X-Registry-Action':'refresh'}
        if cookie and self.cookie:headers['Cookie']=self.cookie
        if isinstance(data,dict):data=json.dumps(data).encode()
        req=Request(self.url+path,headers=headers,data=data)
        try:return build_opener(NoRedirect()).open(req)
        except HTTPError as error:return error
    def login(self):
        with self.request('/login',b'username=neeraj&password=fixture-strong-password') as response:
            self.assertEqual(response.code,303);self.cookie=response.headers['Set-Cookie'].split(';')[0]
            self.assertIn('Secure',response.headers['Set-Cookie']);self.assertIn('HttpOnly',response.headers['Set-Cookie'])
    def test_recovery_page_is_public_but_reset_requires_grant_and_same_origin(self):
        for path in ('/recover','/recovery.js'):
            with self.request(path,cookie=False) as r:self.assertEqual(r.code,200)
        body=b'email=owner%40example.test&token=invalid&new_password=fixture-new-password'
        with self.request('/account/recover',body,origin='https://evil.example',cookie=False) as r:self.assertEqual(r.code,403)
        with self.request('/account/recover',body,cookie=False) as r:self.assertEqual(r.code,403)

    def test_forgot_password_configuration_and_cross_origin(self):
        for path in ('/forgot-password','/forgot-password.js'):
            with self.request(path,cookie=False) as r:self.assertEqual(r.code,200)
        with self.request('/account/forgot-password',b'email=owner%40example.test',cookie=False) as r:
            self.assertEqual(r.code,503);self.assertIn('not configured',json.load(r)['error'])
        with self.request('/account/forgot-password',b'email=owner%40example.test',origin='https://evil.example',cookie=False) as r:self.assertEqual(r.code,403)
        class Reset:
            ready=True
            def request(self,email):return None
        self.server.password_reset=Reset()
        responses=[]
        for email in ('owner%40example.test','missing%40example.test'):
            with self.request('/account/forgot-password',('email='+email).encode(),cookie=False) as r:
                self.assertEqual(r.code,202);responses.append(json.load(r))
        self.assertEqual(responses[0],responses[1])

    def test_graph_scripts_follow_authenticated_application_scope(self):
        for path in ('/graph-model.js','/graph-ui.js','/mapping-model.js','/mapping-ui.js'):
            with self.request(path,cookie=False) as r:self.assertEqual(r.code,303)
        self.login()
        for path in ('/graph-model.js','/graph-ui.js','/mapping-model.js','/mapping-ui.js'):
            with self.request(path) as r:self.assertEqual(r.code,200)

    def test_public_health_and_protected_inventory(self):
        with self.request('/healthz') as r:self.assertEqual(r.code,200)
        with self.request('/api/inventory') as r:self.assertEqual(r.code,401)
        with self.request('/') as r:self.assertEqual(r.code,303);self.assertEqual(r.headers['Location'],'/login')
        self.login()
        with self.request('/api/inventory') as r:self.assertEqual(json.load(r)['agents'],[])
    def test_public_demo_is_static_and_does_not_expose_or_mutate_registry(self):
        self.persistence.store.create({'name':'Private account record'})
        before=self.persistence.store.snapshot()
        for path in ('/demo','/demo/','/demo/style.css','/demo/model.js','/demo/app.js'):
            with self.request(path,cookie=False) as r:
                self.assertEqual(r.code,200)
                body=r.read().decode()
                self.assertNotIn('Private account record',body)
                if path.endswith('.js'):
                    self.assertNotIn('fetch(',body)
                    self.assertNotIn('/api/',body)
        with self.request('/demo',host='evil.example') as r:self.assertEqual(r.code,403)
        with self.request('/api/inventory',cookie=False) as r:self.assertEqual(r.code,401)
        self.assertEqual(self.persistence.store.snapshot(),before)

    def test_login_host_origin_and_forged_cookie(self):
        with self.request('/login',b'username=neeraj&password=wrong') as r:self.assertEqual(r.code,303)
        with self.request('/login',b'username=neeraj&password=fixture-strong-password',origin='https://evil.example') as r:self.assertEqual(r.code,403)
        with self.request('/login',host='evil.example') as r:self.assertEqual(r.code,403)
        self.cookie='catproof_session=invalid.signature'
        with self.request('/api/inventory') as r:self.assertEqual(r.code,401)
    def test_authenticated_write_and_cross_origin_rejection(self):
        self.login()
        with self.request('/api/agents',{'name':'Hosted test agent'}) as r:self.assertEqual(r.code,201)
        with self.request('/api/agents',{'name':'Rejected'},origin='https://evil.example') as r:self.assertEqual(r.code,403)
        with self.request('/api/inventory') as r:self.assertEqual(len(json.load(r)['agents']),1)
    def test_commit_failure_never_returns_success_or_retains_uncommitted_data(self):
        self.login();self.persistence.fail=True
        with self.request('/api/agents',{'name':'Not committed'}) as r:self.assertEqual(r.code,503)
        self.assertEqual(self.persistence.store.snapshot()['agents'],[])
        with self.request('/healthz') as r:self.assertEqual(r.code,503)
    def test_logout_expires_session(self):
        self.login()
        with self.request('/logout',b'{}') as r:self.assertEqual(r.code,303);self.assertIn('Max-Age=0',r.headers['Set-Cookie'])
    def test_rate_limit(self):
        self.server.attempts=[__import__('time').time()]*100
        with self.request('/login',b'username=neeraj&password=wrong') as r:self.assertEqual(r.code,429)
    def test_missing_hosted_configuration_fails_closed(self):
        for env in ({},{'APP_PASSWORD':'fixture-strong-password'},{'APP_PASSWORD':'fixture-strong-password','APP_ORIGIN':'http://example.com'}):
            with self.assertRaises(ValueError):create_server(env,self.persistence,('127.0.0.1',0),accounts=self.accounts)

class FakeDatabase:
    """Fault-injection connection factory backed by real PostgreSQL."""
    def __init__(self):
        from tests.db import database
        self.url,self.factory=database();self.fail=False;self.locks=0
    def connect(self,*args,**kwargs):
        raw=self.factory();parent=self
        class Connection:
            def __getattr__(self,key):return getattr(raw,key)
            def __enter__(self):return self
            def __exit__(self,kind,*rest):
                if kind is None and parent.fail:
                    raw.rollback();raw.close();raise RuntimeError('Commit failed')
                return raw.__exit__(kind,*rest)
            def execute(self,query,args=None):
                if 'pg_advisory' in query:parent.locks+=1
                return raw.execute(query,args)
        # Standalone registry setup sets autocommit; use actual psycopg connection
        # for that connection, inject failures only at later transaction exits.
        if not kwargs:return raw
        return Connection()

class HostedPersistenceTests(unittest.TestCase):
    def test_restart_loads_persisted_records_and_failed_commit_is_not_durable(self):
        database=FakeDatabase();first=HostedStore('fixture',database.connect)
        with first.request(True) as store:store.create({'name':'Durable canonical record'})
        second=HostedStore('fixture',database.connect)
        with second.request() as store:self.assertEqual(store.snapshot()['agents'][0]['name'],'Durable canonical record')
        database.fail=True
        with self.assertRaises(RuntimeError):
            with second.request(True) as store:store.create({'name':'Failed write'})
        database.fail=False
        with second.request() as store:self.assertEqual(len(store.snapshot()['agents']),1)
        self.assertGreaterEqual(database.locks,2)
        first.store.db.close();second.store.db.close()
