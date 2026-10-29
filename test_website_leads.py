import json
import unittest
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from website_leads import validate_lead,WebsiteLeads
import test_hosted

class LeadValidationTests(unittest.TestCase):
    def test_consent_email_bounds_and_honeypot(self):
        valid={'kind':'updates','email':' OWNER@Example.com ','consent':True}
        self.assertEqual(validate_lead(valid)['email'],'owner@example.com')
        for changed in ({'consent':False},{'consent':'true'},{'email':'bad'},{'email':'x\r\n@y.com'},{'email':12},{'kind':'unknown'},{'email':'a'*255+'@example.com'}):
            with self.assertRaises(ValueError):validate_lead(dict(valid,**changed))
        self.assertIsNone(validate_lead(dict(valid,website='spam')))
        with self.assertRaises(ValueError):validate_lead(dict(valid,kind='contact'))

class WebsiteLeadHTTPTests(unittest.TestCase):
    setUp=test_hosted.HostedHTTPTests.setUp
    tearDown=test_hosted.HostedHTTPTests.tearDown
    def lead_request(self,data=None,origin='https://catproof.ai',method='POST',content_type='application/json'):
        request=Request(self.url+'/api/website-leads',data=json.dumps(data).encode() if data is not None else None,
            method=method,headers={'Host':'registry.example','Origin':origin,'Content-Type':content_type})
        try:return urlopen(request)
        except HTTPError as exc:return exc
    def test_preflight_only_trusted_website_and_no_cookie_access(self):
        with self.lead_request(method='OPTIONS') as r:
            self.assertEqual(r.status,204);self.assertEqual(r.headers['Access-Control-Allow-Origin'],'https://catproof.ai')
            self.assertIsNone(r.headers['Access-Control-Allow-Credentials'])
        with self.lead_request(method='OPTIONS',origin='https://evil.example') as r:
            self.assertEqual(r.status,403);self.assertIsNone(r.headers['Access-Control-Allow-Origin'])
        with self.lead_request({'kind':'updates','email':'owner@example.test','consent':True},origin='https://evil.example') as r:self.assertEqual(r.status,403)
    def test_persist_deduplicate_separate_purposes_and_restart(self):
        data={'kind':'updates','email':'OWNER@example.test','consent':True}
        for _ in range(2):
            with self.lead_request(data) as r:self.assertEqual(r.status,202)
        WebsiteLeads(self.accounts).capture(dict(data,kind='contact',name='Owner',company='Example',message='Help with inventory'))
        with self.accounts.connect(self.accounts.url) as db:
            rows=db.execute('SELECT email,kind,policy_version FROM catproof_website_leads ORDER BY kind').fetchall()
            self.assertEqual(rows,[('owner@example.test','contact','2026-10-28'),('owner@example.test','updates','2026-10-28')])
    def test_invalid_honeypot_and_database_failure_never_claim_saved(self):
        valid={'kind':'updates','email':'owner@example.test','consent':True}
        with self.lead_request(dict(valid,consent=False)) as r:self.assertEqual(r.status,400)
        with self.lead_request(dict(valid,website='spam')) as r:self.assertEqual(r.status,202)
        with self.accounts.connect(self.accounts.url) as db:self.assertEqual(db.execute('SELECT count(*) FROM catproof_website_leads').fetchone()[0],0)
        class Broken:
            def rate_allowed(self,email):return True
            def capture(self,data):raise RuntimeError('private database detail')
        self.server.website_leads=Broken()
        with self.lead_request(valid) as r:
            self.assertEqual(r.status,503);self.assertNotIn('private database detail',r.read().decode())
    def test_rate_limit_and_wrong_content_type(self):
        data={'kind':'updates','email':'owner@example.test','consent':True}
        with self.lead_request(data,content_type='text/plain') as r:self.assertEqual(r.status,400)
        self.server.website_leads.rate_allowed=lambda key:False
        with self.lead_request(data) as r:self.assertEqual(r.status,429)
    def test_public_lead_quota_is_separate_from_login_budget(self):
        for _ in range(11):allowed=self.server.website_leads.rate_allowed('owner@example.test')
        self.assertFalse(allowed)
        self.assertTrue(self.accounts.rate_allowed('unrelated-login'))
