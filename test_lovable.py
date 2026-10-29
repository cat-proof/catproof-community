from tests.db import registry as FederatedRegistry
"""Official Lovable API contract fixtures; no account credentials or live claims."""
import tempfile
import unittest
from pathlib import Path
from adapters import read_connector
from adapters.setup import check_setup
from registry import ConnectorError
from test_connectors import FakeTransport

class LovableTests(unittest.TestCase):
    def project(self, **extra):
        return dict(id='project-1',name='Support app',workspace_id='workspace-1',owner_id='user-1',is_published=True,description='Support portal',**extra)
    def page(self,rows,more=False,cursor=None):
        return {'data':rows,'pagination':{'has_more':more,'next_cursor':cursor}}
    def connection(self):
        return dict(id='test',kind='lovable',url='https://api.lovable.dev',credential_prefix='TEST',config={'workspace_id':'workspace-1'},enabled=True)
    def read(self,responses):
        fake=FakeTransport(responses,origin='https://api.lovable.dev');headers={}
        def factory(origin,h):headers.update(h);return fake
        result=read_connector(self.connection(),{'TEST_TOKEN':'fixture-secret'},transport_factory=factory)
        return result,fake,headers
    def test_paginated_assets_not_agents_and_secret_header(self):
        second=self.project();second.update(id='project-2',is_published=False)
        result,t,h=self.read([self.page([self.project()],True,'opaque+cursor'),self.page([second])])
        self.assertEqual(result['agents'],[]);self.assertEqual(len(result['assets']),2)
        a=result['assets'][0];self.assertEqual(a['owner'],'user-1');self.assertEqual(a['status'],'published');self.assertEqual(a['provenance'],'live');self.assertTrue(a['last_seen'])
        self.assertEqual(result['assets'][1]['status'],'unpublished')
        self.assertEqual(h,{'Lovable-API-Key':'fixture-secret','Lovable-Version':'2026-09-11'})
        self.assertEqual(t.calls[1]['params']['cursor'],'opaque+cursor');self.assertEqual(t.calls[1]['params']['workspace_id'],'workspace-1')
        self.assertTrue(all(c['method']=='GET' and c['path']=='/v1/projects' for c in t.calls))
        self.assertNotIn('fixture-secret',str(result))
    def test_empty_inventory(self):
        self.assertEqual(self.read([self.page([])])[0]['assets'],[])
    def test_malformed_partial_or_wrong_scope_fails(self):
        wrong=self.project();wrong['workspace_id']='other'
        invalid=self.project();invalid['is_published']='true'
        for page in [{'data':[]},self.page([wrong]),self.page([invalid]),self.page([self.project(),self.project()]),self.page([],True,None),self.page([],False,'unexpected')]:
            with self.subTest(page=page),self.assertRaises(ConnectorError):self.read([page])
        with self.assertRaises(ConnectorError):self.read([self.page([],True,'same'),self.page([],True,'same')])
    def test_no_credentials_or_unofficial_origin_never_constructs_transport(self):
        for url,env in [('https://api.lovable.dev',{}),('https://evil.example',{'TEST_TOKEN':'fixture-secret'})]:
            c=self.connection();c['url']=url
            def forbidden(*args):self.fail('Must fail before constructing transport')
            with self.assertRaises(ConnectorError):read_connector(c,env,transport_factory=forbidden)
        c=self.connection();c['url']='https://evil.example'
        self.assertFalse(check_setup(c,{'TEST_TOKEN':'fixture-secret'})['ready'])
    def test_failed_refresh_keeps_previous_assets_and_links(self):
        with tempfile.TemporaryDirectory() as d:
            responses=[self.page([self.project()])]
            def reader(c,env,force):return self.read(responses)[0]
            store=FederatedRegistry(Path(d)/'db',env={},reader=reader)
            c=self.connection();c.pop('id');c.pop('enabled');c['name']='My Lovable'
            cid=store.add_connection(c);store.refresh(cid)
            snap=store.snapshot();self.assertEqual(snap['agents'],[]);a=snap['assets'][0]
            eid=store.create({'name':'Support agent','business_owner':'Business owner'});store.link_asset(a['asset_id'],eid)
            responses[:]=[{'data':[]}];store.refresh(cid)
            snap=store.snapshot();self.assertTrue(snap['assets'][0]['stale']);self.assertEqual(snap['assets'][0]['linked_agents'],[eid]);self.assertEqual(snap['agents'][0]['business_owner'],'Business owner')
            store.db.close()


class LovableCredentialVisibilityTests(unittest.TestCase):
    def test_configured_flag_does_not_expose_key_or_claim_validity(self):
        with tempfile.TemporaryDirectory() as folder:
            store=FederatedRegistry(Path(folder)/'db',env={'LOVABLE_TOKEN':'fixture-secret-never-display'})
            store.add_connection({'name':'Lovable','kind':'lovable','url':'https://api.lovable.dev','credential_prefix':'LOVABLE','config':{'workspace_id':'workspace-fixture'}})
            source=store.snapshot()['sources'][0]
            self.assertTrue(source['credential_configured']);self.assertIsNone(source['last_success']);self.assertNotIn('fixture-secret-never-display',str(source));store.db.close()

if __name__=='__main__':unittest.main()
