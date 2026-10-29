import json
import unittest
from cryptography.fernet import Fernet
from connector_secrets import ConnectorSecrets
import test_accounts_http as fixtures

class SecretHTTPTests(unittest.TestCase):
    tearDown=fixtures.AccountHTTPTests.tearDown
    request=fixtures.AccountHTTPTests.request
    signup=fixtures.AccountHTTPTests.signup
    def setUp(self):
        fixtures.AccountHTTPTests.setUp(self);self.server.credentials=ConnectorSecrets(self.accounts,Fernet.generate_key().decode());self.persistence.credentials=self.server.credentials
    def test_customer_connect_save_and_no_readback(self):
        cookie=self.signup('owner@example.test');body={'name':'My Lovable','workspace_id':'workspace-demo','api_key':'fixture-customer-key'}
        with self.request('/api/credentials/connect-lovable',body,cookie) as r:
            self.assertEqual(r.code,200);data=json.load(r);cid=data['connection_id'];self.assertNotIn('fixture-customer-key',str(data))
        with self.request('/api/inventory',cookie=cookie) as r:
            data=json.load(r);self.assertTrue(data['sources'][0]['credential_configured']);self.assertNotIn('fixture-customer-key',str(data))
        with self.request('/api/connection-check',{'connection_id':cid},cookie) as r:self.assertTrue(json.load(r)['ready'])
        with self.request('/api/credentials/save',{'connection_id':cid,'api_key':'fixture-new-key'},cookie) as r:self.assertEqual(r.code,200)
        with self.request('/api/credentials/remove',{'connection_id':cid},cookie) as r:self.assertEqual(r.code,200)
        with self.request('/api/inventory',cookie=cookie) as r:self.assertFalse(json.load(r)['sources'][0]['enabled'])
    def test_csrf_viewer_editor_and_foreign_connection_cannot_change_key(self):
        owner=self.signup('owner@example.test');other=self.signup('other@example.test');body={'name':'Lovable','workspace_id':'workspace-demo','api_key':'fixture-customer-key'}
        with self.request('/api/credentials/connect-lovable',body,owner,origin='https://evil.example') as r:self.assertEqual(r.code,403)
        with self.request('/api/credentials/connect-lovable',body,owner) as r:cid=json.load(r)['connection_id']
        with self.request('/api/credentials/save',{'connection_id':cid,'api_key':'fixture-other-key'},other) as r:self.assertEqual(r.code,400)
        with self.request('/api/security/invite',{'email':'other@example.test','role':'editor','password':'fixture-personal-password'},owner) as r:inv=json.load(r)
        with self.request('/api/security/accept',{'code':inv['code']},other):pass
        with self.request('/api/credentials/save',{'connection_id':cid,'api_key':'fixture-other-key'},other) as r:self.assertEqual(r.code,403)
        with self.request('/api/credentials/remove',{'connection_id':cid},other) as r:self.assertEqual(r.code,403)
    def test_generic_auth_create_replace_remove_and_reject_cross_origin(self):
        cookie=self.signup('owner@example.test')
        body={'name':'Synthetic ServiceNow','kind':'servicenow','url':'https://synthetic.example.test','credentials':{'mode':'basic','username':'fixture-reader','password':'fixture-private-password'}}
        with self.request('/api/credentials/connect',body,cookie,origin='https://evil.example') as r:self.assertEqual(r.code,403)
        with self.request('/api/credentials/connect',body,cookie) as r:
            self.assertEqual(r.code,200);cid=json.load(r)['connection_id']
        with self.request('/api/connection-check',{'connection_id':cid},cookie) as r:self.assertTrue(json.load(r)['ready'])
        with self.request('/api/inventory',cookie=cookie) as r:
            inv=json.load(r);self.assertTrue(inv['sources'][0]['credential_configured']);self.assertNotIn('fixture-private-password',str(inv));self.assertNotIn('fixture-reader',str(inv))
        with self.request('/api/credentials/save',{'connection_id':cid,'credentials':{'mode':'token','token':'fixture-token-secret'}},cookie) as r:self.assertEqual(r.code,200)
        with self.request('/api/credentials/remove',{'connection_id':cid},cookie) as r:self.assertEqual(r.code,200)
        with self.request('/api/inventory',cookie=cookie) as r:
            source=json.load(r)['sources'][0];self.assertFalse(source['enabled']);self.assertFalse(source['credential_configured'])
