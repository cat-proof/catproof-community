"""Preview isolation, atomic record tools and conditional linking against disposable PostgreSQL."""
import json
import threading
import unittest
from unittest.mock import patch
from tests.db import registry as registry_fixture
from test_federation import payload, record
import test_hosted_sync
from enterprise import can_write


class RecordWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.store=registry_fixture()
        self.addCleanup(self.store.db.close)

    def test_reconciliation_approval_and_stale_decisions(self):
        eid=self.store.create({'name':'Official','business_owner':'Team','purpose':'Help customers'})
        a=self.store.snapshot()['agents'][0]
        result=self.store.reconcile({'registry_id':eid,'expected_updated_at':a['updated_at'],'expected_record':{k:a[k] for k in ('name','purpose','business_owner','lifecycle','review','notes')},'decision':'approve'})
        self.assertEqual(result['agents'][0]['review'],'reviewed')
        with self.assertRaises(ValueError):self.store.reconcile({'registry_id':eid,'expected_updated_at':a['updated_at'],'expected_record':{k:a[k] for k in ('name','purpose','business_owner','lifecycle','review','notes')},'decision':'approve'})
        cid=self.store.add_connection({'name':'Source','kind':'atlas','url':'https://fixture.invalid'})
        self.store.ingest(cid,payload(record()))
        o=self.store.snapshot()['observations'][0]
        self.store.link(o['observation_id'],eid)
        a=next(a for a in self.store.snapshot()['agents'] if a['registry_id']==eid)
        decision={'registry_id':eid,'expected_updated_at':a['updated_at'],'expected_record':{k:a[k] for k in ('name','purpose','business_owner','lifecycle','review','notes')},'decision':'accept','field':'name','observation_id':o['observation_id'],'expected_observed':o['name']}
        with self.assertRaises(ValueError):self.store.reconcile(dict(decision,expected_observed='Changed'))
        result=self.store.reconcile(decision)
        self.assertEqual(next(a for a in result['agents'] if a['registry_id']==eid)['name'],o['name'])
        self.assertTrue(any(e['kind']=='reconciled' for e in result['events']))
        with self.assertRaises(KeyError):self.store.reconcile(dict(decision,registry_id='foreign'))

    def test_reconciliation_requires_owner_purpose_and_current_source(self):
        eid=self.store.create({'name':'Incomplete'})
        a=self.store.snapshot()['agents'][0]
        expected={k:a[k] for k in ('name','purpose','business_owner','lifecycle','review','notes')}
        with self.assertRaises(ValueError):self.store.reconcile({'registry_id':eid,'expected_updated_at':a['updated_at'],'expected_record':expected,'decision':'approve'})
        cid=self.store.add_connection({'name':'Source','kind':'atlas','url':'https://fixture.invalid'})
        self.store.ingest(cid,payload(record()))
        o=self.store.snapshot()['observations'][0]
        self.store.link(o['observation_id'],eid)
        self.store.set_enabled(cid,False)
        with self.assertRaises(ValueError):self.store.reconcile({'registry_id':eid,'expected_updated_at':a['updated_at'],'expected_record':expected,'decision':'accept','field':'name','observation_id':o['observation_id'],'expected_observed':o['name']})
        self.assertEqual(next(x for x in self.store.snapshot()['agents'] if x['registry_id']==eid)['name'],'Incomplete')
        self.assertFalse(can_write('viewer','/api/reconcile'))
        self.assertTrue(can_write('editor','/api/reconcile'))

    def test_source_owner_can_be_adopted_without_approving_agent(self):
        cid=self.store.add_connection({'name':'Source','kind':'atlas','url':'https://fixture.invalid'})
        self.store.ingest(cid,payload(record()))
        a=self.store.snapshot()['agents'][0];o=a['linked_records'][0]
        request={'registry_id':a['registry_id'],'expected_updated_at':a['updated_at'],'expected_record':{k:a[k] for k in ('name','purpose','business_owner','lifecycle','review','notes')},'decision':'accept','field':'business_owner','observation_id':o['observation_id'],'expected_observed':o['owner']}
        result=self.store.reconcile(request);saved=result['agents'][0]
        self.assertEqual(saved['business_owner'],'source-owner');self.assertEqual(saved['review'],'unreviewed')
        changed=record();changed['owner']='New source owner';self.store.ingest(cid,payload(changed))
        saved=self.store.snapshot()['agents'][0]
        self.assertEqual(saved['business_owner'],'source-owner');self.assertEqual(saved['linked_records'][0]['owner'],'New source owner')
        changed['owner']='Unassigned';self.store.ingest(cid,payload(changed))
        request.update(expected_updated_at=saved['updated_at'],expected_record={k:saved[k] for k in ('name','purpose','business_owner','lifecycle','review','notes')},expected_observed='Unassigned')
        with self.assertRaises(ValueError):self.store.reconcile(request)

    def test_import_validates_entire_file_before_writing(self):
        with self.assertRaises(ValueError):self.store.import_agents([{'name':'First'},{'name':'Bad','lifecycle':'invalid'}])
        self.assertEqual(self.store.snapshot()['agents'],[])
        result=self.store.import_agents([{'name':'A','business_owner':'Finance'},{'name':'A','notes':'Deliberate separate record'}])
        self.assertEqual(len(result['imported_ids']),2)
        self.assertEqual(len({a['registry_id'] for a in result['agents']}),2)
        self.assertTrue(all(a['provenance']=='manual' for a in result['agents']))
        self.assertEqual(len([e for e in result['events'] if e['detail'].get('method')=='CSV import']),2)

    def test_import_rolls_back_a_late_failure_and_its_events(self):
        original=self.store._create
        def create(row):
            if row['name']=='Bad':raise ValueError('Synthetic failure')
            return original(row)
        with patch.object(self.store,'_create',side_effect=create):
            with self.assertRaises(ValueError):self.store.import_agents([{'name':'Good'},{'name':'Bad'}])
        self.assertEqual(self.store.snapshot()['agents'],[])
        self.assertEqual(self.store.snapshot()['events'],[])

    def test_bulk_owner_is_atomic_and_preserves_other_fields(self):
        a=self.store.create({'name':'A','purpose':'Keep this','business_owner':'Old'})
        b=self.store.create({'name':'B'})
        with self.assertRaises(KeyError):self.store.assign_owner([a,'foreign-or-missing'],'New')
        self.assertEqual(next(x for x in self.store.snapshot()['agents'] if x['registry_id']==a)['business_owner'],'Old')
        result=self.store.assign_owner([a,b],' New team ')
        self.assertTrue(all(x['business_owner']=='New team' for x in result['agents']))
        self.assertEqual(next(x for x in result['agents'] if x['registry_id']==a)['purpose'],'Keep this')
        self.assertEqual(len([e for e in result['events'] if e['kind']=='registry_updated']),2)
        with self.assertRaises(ValueError):self.store.assign_owner([a,a],'Owner')

    def test_undo_link_checks_current_binding_and_keeps_both_records(self):
        cid=self.store.add_connection({'name':'Source','kind':'atlas','url':'https://fixture.invalid'})
        self.store.ingest(cid,payload(record()))
        before=self.store.snapshot();o=before['observations'][0];old=o['registry_id']
        target=self.store.create({'name':'Target'})
        self.store.link(o['observation_id'],target,old)
        with self.assertRaises(ValueError):self.store.link(o['observation_id'],old,old)
        result=self.store.link(o['observation_id'],old,target)
        self.assertEqual(result['observations'][0]['registry_id'],old)
        self.assertEqual(len(result['agents']),2)


class PreviewWorkflowTests(unittest.TestCase):
    setUp=test_hosted_sync.HostedSyncTests.setUp
    tearDown=test_hosted_sync.HostedSyncTests.tearDown
    signup=test_hosted_sync.HostedSyncTests.signup
    join=test_hosted_sync.HostedSyncTests.join
    connection=test_hosted_sync.HostedSyncTests.connection
    authorize=test_hosted_sync.HostedSyncTests.authorize

    def test_preview_does_not_write_or_expose_provider_metadata(self):
        cid=self.connection()
        with self.store.request(False,self.a['workspace_id']) as r:before=r.snapshot()
        response=payload(record());response['agents'][0]['evidence']={'secret':'never-return'}
        result=self.store.preview(self.a['workspace_id'],cid,self.authorize,lambda *args:response)
        self.assertEqual(result['agent_count'],1)
        self.assertNotIn('never-return',json.dumps(result))
        with self.store.request(False,self.a['workspace_id']) as r:self.assertEqual(r.snapshot(),before)
        with self.assertRaises(ValueError):self.store.preview(self.b['workspace_id'],cid,self.authorize,lambda *args:response)

    def test_preview_rechecks_authorization_after_fetch(self):
        cid=self.connection();calls=[]
        def authorize():
            calls.append(True)
            if len(calls)>1:raise PermissionError('Revoked')
            return self.a
        with self.assertRaises(PermissionError):self.store.preview(self.a['workspace_id'],cid,authorize,lambda *args:payload(record()))
        with self.store.request(False,self.a['workspace_id']) as r:self.assertEqual(r.snapshot()['agents'],[])

    def test_blocked_preview_allows_edits_and_pause_invalidates_result(self):
        cid=self.connection();entered=threading.Event();release=threading.Event();errors=[]
        def fetch(*args):
            entered.set()
            if not release.wait(5):raise RuntimeError('Test timeout')
            return payload(record())
        def preview():
            try:self.store.preview(self.a['workspace_id'],cid,self.authorize,fetch)
            except Exception as e:errors.append(e)
        worker=threading.Thread(target=preview);worker.start()
        self.addCleanup(lambda:(release.set(),worker.join(6)))
        self.assertTrue(entered.wait(3))
        with self.store.request(True,self.a['workspace_id']) as r:
            r.create({'name':'Edit while reading'});r.set_enabled(cid,False)
        release.set();worker.join(6)
        self.assertEqual(len(errors),1);self.assertIsInstance(errors[0],ValueError)
        with self.store.request(False,self.a['workspace_id']) as r:self.assertEqual([a['name'] for a in r.snapshot()['agents']],['Edit while reading'])


class WorkflowPermissionTests(unittest.TestCase):
    def test_mutations_and_provider_preview_are_editor_only(self):
        for path in ('/api/connection-preview','/api/agents/import','/api/agents/assign-owner'):
            self.assertTrue(can_write('owner',path));self.assertTrue(can_write('editor',path));self.assertFalse(can_write('viewer',path))

class WorkflowHTTPTests(unittest.TestCase):
    setUp=test_hosted_sync.HostedSyncTests.setUp
    tearDown=test_hosted_sync.HostedSyncTests.tearDown
    signup=test_hosted_sync.HostedSyncTests.signup
    join=test_hosted_sync.HostedSyncTests.join
    connection=test_hosted_sync.HostedSyncTests.connection

    def start_server(self):
        from hosted_app import create_server
        server=create_server({'APP_PASSWORD':'fixture-bootstrap-password','APP_ORIGIN':'https://registry.example'},self.store,('127.0.0.1',0),accounts=self.accounts)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        self.addCleanup(lambda:(server.shutdown(),server.server_close(),worker.join(3)))
        return server

    def post(self,server,path,body,token=None):
        from urllib.request import Request,urlopen
        req=Request('http://127.0.0.1:'+str(server.server_port)+path,data=json.dumps(body).encode(),headers={'Host':'registry.example','Origin':'https://registry.example','X-Registry-Action':'refresh','Content-Type':'application/json','Cookie':'catproof_session='+(token or self.at)})
        with urlopen(req) as response:return json.load(response)

    def test_import_and_owner_routes_enforce_tenant_and_viewer_permissions(self):
        from urllib.error import HTTPError
        self.join('viewer');server=self.start_server()
        with self.assertRaises(HTTPError) as error:self.post(server,'/api/agents/import',{'rows':[{'name':'Denied'}]},self.bt)
        self.assertEqual(error.exception.code,403)
        result=self.post(server,'/api/agents/import',{'rows':[{'name':'CSV agent'}]});eid=result['imported_ids'][0]
        result=self.post(server,'/api/agents/assign-owner',{'ids':[eid],'owner':'Operations'})
        self.assertEqual(result['agents'][0]['business_owner'],'Operations')
        with self.store.request(True,self.b['workspace_id']) as r:foreign=r.create({'name':'Other tenant'})
        with self.assertRaises(HTTPError) as error:self.post(server,'/api/agents/assign-owner',{'ids':[eid,foreign],'owner':'Wrong'})
        self.assertEqual(error.exception.code,404)
        with self.store.request(False,self.a['workspace_id']) as r:self.assertEqual(r.snapshot()['agents'][0]['business_owner'],'Operations')

    def test_http_preview_is_non_mutating_and_denies_viewers(self):
        from urllib.error import HTTPError
        cid=self.connection();self.join('viewer');server=self.start_server()
        with self.assertRaises(HTTPError) as error:self.post(server,'/api/connection-preview',{'connection_id':cid},self.bt)
        self.assertEqual(error.exception.code,403)
        with patch('sync_worker.fetch_snapshot',return_value=payload(record())):
            result=self.post(server,'/api/connection-preview',{'connection_id':cid})
        self.assertEqual(result['agent_count'],1)
        with self.store.request(False,self.a['workspace_id']) as r:self.assertEqual(r.snapshot()['agents'],[])
