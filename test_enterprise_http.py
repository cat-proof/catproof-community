import json
import unittest
import test_accounts_http as fixtures

class EnterpriseHTTPTests(unittest.TestCase):
    setUp=fixtures.AccountHTTPTests.setUp
    tearDown=fixtures.AccountHTTPTests.tearDown
    request=fixtures.AccountHTTPTests.request
    signup=fixtures.AccountHTTPTests.signup
    def prepare(self,role='viewer'):
        self.owner=self.signup('owner@example.test');self.member=self.signup('member@example.test')
        self.a=self.accounts.resolve(self.owner.split('=',1)[1]);self.b=self.accounts.resolve(self.member.split('=',1)[1])
        with self.request('/api/security/invite',{'email':'member@example.test','role':role,'password':'fixture-personal-password'},self.owner) as r:
            self.assertEqual(r.code,201);invitation=json.load(r)
        with self.request('/api/security/accept',{'code':invitation['code']},self.member) as r:self.assertEqual(r.code,200)
    def test_viewer_reads_but_cannot_mutate_or_escalate(self):
        self.prepare()
        with self.request('/api/agents',{'name':'Visible record'},self.owner) as r:self.assertEqual(r.code,201)
        with self.request('/api/inventory',cookie=self.member) as r:self.assertEqual(json.load(r)['agents'][0]['name'],'Visible record')
        for path,body in [('/api/agents',{'name':'Denied'}),('/api/connections',{}),('/api/refresh',{}),('/api/security/invite',{'email':'other@example.test','password':'fixture-personal-password'}),('/api/security/member',{'user_id':self.b['id'],'role':'owner','password':'fixture-personal-password'})]:
            with self.request(path,body,self.member) as r:self.assertEqual(r.code,403,path)
        with self.request('/api/security/audit',cookie=self.member) as r:self.assertEqual(r.code,403)
        with self.request('/api/security',cookie=self.member) as r:self.assertNotIn('events',json.load(r))
    def test_editor_cannot_manage_connections_or_keep_old_role(self):
        self.prepare('editor')
        with self.request('/api/agents',{'name':'Editor record'},self.member) as r:self.assertEqual(r.code,201)
        with self.request('/api/connections',{},self.member) as r:self.assertEqual(r.code,403)
        with self.request('/api/security/member',{'user_id':self.b['id'],'role':'viewer','password':'fixture-personal-password'},self.owner) as r:self.assertEqual(r.code,200)
        with self.request('/api/agents',{'name':'Denied after downgrade'},self.member) as r:self.assertEqual(r.code,403)
        with self.request('/api/security/member',{'user_id':self.b['id'],'role':'remove','password':'fixture-personal-password'},self.owner) as r:self.assertEqual(r.code,200)
        with self.request('/api/inventory',cookie=self.member) as r:self.assertEqual(r.code,401)
    def test_actor_and_security_export_are_scoped(self):
        self.prepare('editor')
        with self.request('/api/agents',{'name':'Editor authored'},self.member) as r:self.assertEqual(r.code,201)
        with self.request('/api/history',cookie=self.owner) as r:self.assertEqual(json.load(r)['events'][0]['actor'],'member@example.test')
        with self.request('/api/security/audit',cookie=self.owner) as r:self.assertTrue(any(e['action']=='invitation.accepted' for e in json.load(r)['events']))
        stranger=self.signup('stranger@example.test')
        with self.request('/api/security/audit',cookie=stranger) as r:self.assertFalse(any(e['action']=='invitation.accepted' for e in json.load(r)['events']))
    def test_bad_inputs_and_origin_cannot_change_access(self):
        self.prepare()
        with self.request('/api/security/member',{'user_id':self.b['id'],'role':'owner','password':'fixture-personal-password'},self.owner,origin='https://evil.example') as r:self.assertEqual(r.code,403)
        with self.request('/api/security/invite',{'email':123},self.owner) as r:self.assertEqual(r.code,400)
        with self.request('/api/security/invite',{'email':'other@example.test','role':'viewer','password':'wrong-password'},self.owner) as r:self.assertEqual(r.code,403)
    def test_signup_copy_and_security_headers(self):
        for path in ('/login','/signup'):
            with self.request(path) as r:
                body=r.read();self.assertNotIn(b'workspace',body.replace(b'name="workspace"',b''));self.assertEqual(r.headers['X-Frame-Options'],'DENY');self.assertIn('camera=()',r.headers['Permissions-Policy'])
    def test_new_registry_does_not_show_legacy_team_migration_settings(self):
        cookie=self.signup('new-company@example.test')
        with self.request('/api/security',cookie=cookie) as r:
            data=json.load(r);self.assertFalse(data['bootstrap_enabled']);self.assertFalse(data['bootstrap_account'])
