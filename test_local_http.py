import json
import unittest
from urllib.request import Request,build_opener
from urllib.error import HTTPError
import test_accounts_http as fixtures
from test_accounts_http import NoRedirect
from test_local_devices import report

class LocalHTTPTests(unittest.TestCase):
    setUp=fixtures.AccountHTTPTests.setUp
    tearDown=fixtures.AccountHTTPTests.tearDown
    request=fixtures.AccountHTTPTests.request
    signup=fixtures.AccountHTTPTests.signup
    def collector(self,path,data,token='',origin=None):
        h={'Host':'registry.example','Content-Type':'application/json'}
        if token:h['Authorization']='Bearer '+token
        if origin:h['Origin']=origin
        try:return build_opener(NoRedirect()).open(Request(self.url+path,data=json.dumps(data).encode(),headers=h))
        except HTTPError as err:return err
    def test_end_to_end_enroll_report_read_and_revoke(self):
        owner=self.signup('owner@example.test');other=self.signup('other@example.test')
        with self.request('/api/local/pair',{'label':'QA laptop','password':'fixture-personal-password'},owner) as r:
            self.assertEqual(r.code,201);pair=json.load(r)
        with self.collector('/api/local/enroll',{'code':pair['code']}) as r:self.assertEqual(r.code,201);device=json.load(r)
        with self.collector('/api/local/report',report(),device['token']) as r:self.assertEqual(r.code,200)
        with self.request('/api/local/devices',cookie=owner) as r:
            listing=json.load(r);self.assertEqual(listing['devices'][0]['status'],'reporting');self.assertNotIn(device['token'],str(listing))
        with self.request('/api/local/devices',cookie=other) as r:self.assertEqual(json.load(r)['devices'],[])
        with self.request('/api/local/revoke',{'id':device['device_id'],'password':'fixture-personal-password'},owner) as r:self.assertEqual(r.code,200)
        with self.collector('/api/local/report',report(2),device['token']) as r:self.assertEqual(r.code,401)
        with self.request('/api/inventory',cookie=owner) as r:self.assertEqual(json.load(r)['agents'],[])
    def test_browser_origin_device_token_and_cookie_boundaries(self):
        owner=self.signup('owner@example.test')
        with self.request('/api/local/pair',{'label':'QA','password':'fixture-personal-password'},owner,origin='https://evil.example') as r:self.assertEqual(r.code,403)
        with self.request('/api/local/pair',{'label':'QA','password':'fixture-personal-password'},owner) as r:p=json.load(r)
        with self.collector('/api/local/enroll',{'code':p['code']},origin='https://registry.example') as r:self.assertEqual(r.code,403)
        with self.collector('/api/local/enroll',{'code':p['code']}) as r:device=json.load(r)
        with self.collector('/api/local/report',report(),origin='https://evil.example',token=device['token']) as r:self.assertEqual(r.code,403)
        with self.request('/api/local/devices',cookie='catproof_session='+device['token']) as r:self.assertEqual(r.code,401)
        with self.collector('/api/agents',{'name':'Escalation'},device['token']) as r:self.assertEqual(r.code,403)
        with self.collector('/api/local/report',report()) as r:self.assertEqual(r.code,401)
        with self.request('/api/local/enroll',{'code':p['code']},owner) as r:self.assertEqual(r.code,403)
    def test_viewer_can_read_not_enroll_download_no_secret(self):
        owner=self.signup('owner@example.test');member=self.signup('member@example.test')
        with self.request('/api/security/invite',{'email':'member@example.test','role':'viewer','password':'fixture-personal-password'},owner) as r:invite=json.load(r)
        with self.request('/api/security/accept',{'code':invite['code']},member):pass
        with self.request('/api/local/devices',cookie=member) as r:self.assertEqual(r.code,200)
        with self.request('/api/local/pair',{'label':'QA','password':'fixture-personal-password'},member) as r:self.assertEqual(r.code,403)
        for path in ('/local/collector.py','/local/guide.md','/local.js'):
            with self.request(path,cookie=owner) as r:self.assertEqual(r.code,200);self.assertIn('noindex',r.headers['X-Robots-Tag'])
        with self.request('/local/collector.py') as r:self.assertEqual(r.code,303)
    def test_bad_payload_and_report_throttle_are_actionable(self):
        owner=self.signup('owner@example.test')
        with self.request('/api/local/pair',{'label':'QA','password':'fixture-personal-password'},owner) as r:p=json.load(r)
        with self.collector('/api/local/enroll',{'code':p['code']}) as r:d=json.load(r)
        data=report();data['prompt']='must not persist'
        with self.collector('/api/local/report',data,d['token']) as r:self.assertEqual(r.code,400)
        with self.collector('/api/local/report',report(),d['token']) as r:self.assertEqual(r.code,200)
        with self.collector('/api/local/report',report(2),d['token']) as r:self.assertEqual(r.code,429);self.assertEqual(r.headers['Retry-After'],'30')
