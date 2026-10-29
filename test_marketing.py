"""Public crawl surface stays separate from authenticated account data."""
import json
import unittest
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from urllib.request import Request,build_opener
import test_accounts_http as fixtures
from marketing import ASSETS,PAGES,public_page

class Document(HTMLParser):
    def __init__(self,source):
        super().__init__();self.tags=[];self.feed(source)
    def handle_starttag(self,tag,attrs):self.tags.append((tag,dict(attrs)))

class MarketingHTTPTests(unittest.TestCase):
    setUp=fixtures.AccountHTTPTests.setUp
    tearDown=fixtures.AccountHTTPTests.tearDown
    request=fixtures.AccountHTTPTests.request
    def test_canonical_pages_have_visible_content_and_honest_schema(self):
        for path in PAGES:
            with self.request(path,host='catproof.ai') as response:
                self.assertEqual(response.code,200);self.assertIsNone(response.headers.get('X-Robots-Tag'))
                source=response.read().decode();doc=Document(source)
                self.assertEqual(len([t for t,a in doc.tags if t=='h1']),1)
                self.assertIn(('link',{'rel':'canonical','href':'https://catproof.ai'+path}),doc.tags)
                schema=json.loads(source.split('<script type="application/ld+json">')[1].split('</script>')[0])
                self.assertTrue(schema['@graph']);self.assertNotIn('aggregateRating',source)
                self.assertNotIn('127.0.0.1',source)
                self.assertIn('/assets/catproof-mark.svg',source)
    def test_robots_sitemap_and_private_pages(self):
        with self.request('/robots.txt',host='catproof.ai') as response:self.assertIn(b'Sitemap: https://catproof.ai/sitemap.xml',response.read())
        with self.request('/robots.txt') as response:self.assertEqual(response.read(),b'User-agent: *\nDisallow: /\n')
        with self.request('/sitemap.xml',host='catproof.ai') as response:
            urls=[e.text for e in ET.fromstring(response.read()).iter() if e.tag.endswith('}loc')]
            self.assertEqual(urls,['https://catproof.ai'+p for p in PAGES])
        for path in ('/login','/signup','/demo','/api/inventory','/'):
            with self.request(path) as response:self.assertIn('noindex',response.headers['X-Robots-Tag'])
        with self.request('/llms.txt',host='catproof.ai') as response:self.assertIn(b'interactive demo is fictional',response.read())
    def test_brand_assets_and_head_have_no_account_dependency(self):
        for path in ASSETS:
            with self.request(path) as response:
                self.assertEqual(response.code,200);body=response.read();self.assertGreater(len(body),100)
                if path.endswith('.png'):self.assertTrue(body.startswith(b'\x89PNG\r\n\x1a\n'))
        with build_opener(fixtures.NoRedirect()).open(Request(self.url+'/favicon.ico',headers={'Host':'registry.example'},method='HEAD')) as response:
            self.assertEqual(response.code,200);self.assertEqual(response.read(),b'');self.assertGreater(int(response.headers['Content-Length']),100)
        with self.request('/site.webmanifest') as response:self.assertEqual(json.load(response)['name'],'CatProof')
    def test_canonical_domain_redirects_and_real_404(self):
        self.server.marketing_origin='https://catproof.ai';self.server.hosts.add('www.catproof.ai')
        with self.request('/welcome') as response:self.assertEqual(response.code,301);self.assertEqual(response.headers['Location'],'https://catproof.ai/')
        with self.request('/connectors') as response:self.assertEqual(response.code,301);self.assertEqual(response.headers['Location'],'https://catproof.ai/connectors')
        with self.request('/',host='www.catproof.ai') as response:self.assertEqual(response.code,301);self.assertEqual(response.headers['Location'],'https://catproof.ai/')
        with self.request('/missing-page',host='catproof.ai') as response:self.assertEqual(response.code,404)
        with self.request('/assets/../accounts.py',host='catproof.ai') as response:self.assertEqual(response.code,404)
        with self.request('/api/inventory') as response:self.assertEqual(response.code,401)
        with self.request('/signup',{'name':'Public host'},host='catproof.ai',form=True) as response:self.assertEqual(response.code,404)
    def test_public_responses_do_not_initialize_a_workspace(self):
        for path in ('/','/connectors','/guides/agent-system-of-record','/demo'):
            with self.request(path,host='catproof.ai') as response:self.assertEqual(response.code,200)
        with self.accounts.connect(self.accounts.url) as conn:
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM catproof_users').fetchone()[0],0)
    def test_dynamic_app_origin_is_escaped(self):
        source=public_page('/','https://example.test/\"<script>')
        self.assertNotIn('href="https://example.test/"<script>',source)
