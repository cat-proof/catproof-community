"""Regressions for the six connector review findings."""
import unittest
from unittest.mock import patch
from adapters.transport import Response
from registry import JSONResponse,ConnectorError,servicenow
import test_connectors as connector_fixtures
import test_connector_secrets as secret_fixtures
import test_hosted_sync as sync_fixtures
from tests.db import registry

class ProviderContractTests(unittest.TestCase):
    def test_servicenow_follows_short_and_empty_pages(self):
        seen=[]
        def fetch(host,path,token,params):
            offset=params['sysparm_offset'];seen.append(offset)
            rows=[] if offset==100 else [{'sys_id':str(offset),'name':'Visible agent'}]
            headers={'Link':f'<{host}{path}?sysparm_limit=100&sysparm_offset={offset+100}>;rel="next"'} if offset<200 else {}
            return JSONResponse({'result':rows},headers)
        rows=servicenow({'SERVICENOW_INSTANCE_URL':'https://fixture.invalid','SERVICENOW_TOKEN':'fixture'},fetch)
        self.assertEqual(seen,[0,100,200]);self.assertEqual([r['source_id'] for r in rows],['0','200'])
    def test_servicenow_rejects_foreign_or_repeated_next_link(self):
        for target in ('https://evil.invalid/api/now/table/sn_aia_agent?sysparm_limit=100&sysparm_offset=100','https://fixture.invalid/api/now/table/sn_aia_agent?sysparm_limit=100&sysparm_offset=0'):
            with self.subTest(target=target),self.assertRaises(ConnectorError):
                servicenow({'SERVICENOW_INSTANCE_URL':'https://fixture.invalid','SERVICENOW_TOKEN':'fixture'},lambda *args:JSONResponse({'result':[]},{'Link':f'<{target}>;rel="next"'}))
    def test_salesforce_malformed_response_retains_inventory(self):
        c=connector_fixtures.ConnectorTests()
        good='<Envelope xmlns="http://schemas.xmlsoap.org/soap/envelope/" xmlns:m="http://soap.sforce.com/2006/04/metadata"><Body><m:listMetadataResponse><m:result><m:id>a</m:id><m:fullName>Agent</m:fullName></m:result></m:listMetadataResponse></Body></Envelope>'
        store=registry();self.addCleanup(store.db.close)
        cid=store.add_connection({'name':'Salesforce','kind':'salesforce','url':'https://fixture.invalid','credential_prefix':'SF','config':{'api_version':'68.0'}})
        result,_,_=c.read('salesforce',[Response(None,text=good)],{'api_version':'68.0'});store.ingest(cid,result)
        store.reader=lambda *args:c.read('salesforce',[Response(None,text='<Envelope xmlns="http://schemas.xmlsoap.org/soap/envelope/"/>')],{'api_version':'68.0'})[0]
        result=store.refresh(cid)
        self.assertTrue(result['observations'][0]['present']);self.assertTrue(result['observations'][0]['stale']);self.assertEqual(result['runs'][0]['outcome'],'failed')
        empty=good.replace('<m:result><m:id>a</m:id><m:fullName>Agent</m:fullName></m:result>','')
        self.assertEqual(c.read('salesforce',[Response(None,text=empty)],{'api_version':'68.0'})[0]['agents'],[])
    def test_mcp_new_version_preserves_explicit_old_version_link(self):
        c=connector_fixtures.ConnectorTests();store=registry();self.addCleanup(store.db.close)
        cid=store.add_connection({'name':'MCP','kind':'mcp','url':'https://fixture.invalid'})
        def read(versions):return c.read('mcp',[{'servers':[{'server':{'name':'org/tool','version':v}} for v in versions],'metadata':{}}])
        first,transport,_=read(['1']);self.assertNotIn('version',transport.calls[0]['params'])
        store.ingest(cid,first);aid=store.snapshot()['assets'][0]['asset_id'];eid=store.create({'name':'Agent'});store.link_asset(aid,eid)
        second,_,_=read(['1','2']);store.ingest(cid,second)
        old=next(a for a in store.snapshot()['assets'] if a['asset_id']==aid)
        self.assertTrue(old['present']);self.assertEqual(old['linked_agents'],[eid])

class ScopeTests(unittest.TestCase):
    setUp=secret_fixtures.SecretTests.setUp
    tearDown=secret_fixtures.SecretTests.tearDown
    signup=secret_fixtures.SecretTests.signup
    initialize_secrets=secret_fixtures.SecretTests.initialize_secrets
    def test_two_accounts_same_endpoint_do_not_replace_each_other(self):
        for kind,key,values,extra in [('copilot','tenant_id',['00000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-000000000002'],{}),('aws_bedrock','account_id',['123456789012','234567890123'],{'region':'us-east-1'})]:
            ids=[]
            for value in values:
                credentials={'mode':'token','token':'fixture-'+value} if kind=='copilot' else {'mode':'aws','access_key_id':'fixture-'+value,'secret_access_key':'fixture-secret'}
                body={'name':value,'kind':kind,'url':'https://shared.invalid','config':{**extra,key:value},'credentials':credentials}
                ids.append(self.secrets.change(self.a,body,connect=True)['connection_id'])
                with self.assertRaises(ValueError):self.secrets.change(self.a,body,connect=True)
            self.assertEqual(len(set(ids)),2)
            with self.store.request(False,self.a['workspace_id']) as store:
                self.assertTrue(all(store.credentials[cid] for cid in ids))
            with self.store.request(True,self.a['workspace_id']) as store:
                for cid in ids:store.set_archived(cid,True)

class SchedulingTests(unittest.TestCase):
    setUp=sync_fixtures.HostedSyncTests.setUp
    tearDown=sync_fixtures.HostedSyncTests.tearDown
    signup=sync_fixtures.HostedSyncTests.signup
    join=sync_fixtures.HostedSyncTests.join
    authorize=sync_fixtures.HostedSyncTests.authorize
    def test_deferred_sources_are_untouched_and_run_first_next_time(self):
        # Exercise scheduler fairness independently of Community's two-slot policy.
        self.store.connection_limit=None
        with self.store.request(True,self.a['workspace_id']) as store:
            ids=[store.add_connection({'name':str(i),'kind':'discovery','url':f'https://source{i}.invalid'}) for i in range(3)]
        clock=[0];called=[]
        def fetch(job,env,timeout):called.append(job['id']);clock[0]+=30;return {'agents':[],'assets':[]}
        with patch('time.monotonic',side_effect=lambda:clock[0]):result=self.store.refresh(self.a['workspace_id'],None,self.authorize,fetch)
        deferred=next(s for s in result['sources'] if s['id'] not in called)
        self.assertIsNone(deferred['last_attempt']);self.assertIsNone(deferred['error']);self.assertEqual(len(result['runs']),2)
        called.clear()
        with patch('time.monotonic',side_effect=lambda:clock[0]):self.store.refresh(self.a['workspace_id'],None,self.authorize,fetch)
        self.assertEqual(called[0],deferred['id'])

    def test_deferred_source_does_not_invalidate_another_sync(self):
        with self.store.request(True,self.a['workspace_id']) as store:
            first=store.add_connection({'name':'First','kind':'discovery','url':'https://first.invalid'})
            deferred=store.add_connection({'name':'Deferred','kind':'discovery','url':'https://later.invalid'})
            store.db.execute("UPDATE catproof_connections SET last_attempt='2026-01-01',sync_generation='another-inflight-sync' WHERE id=?",(deferred,))
        clock=[0]
        def fetch(job,env,timeout):
            self.assertEqual(job['id'],first);clock[0]+=60;return {'agents':[],'assets':[]}
        with patch('time.monotonic',side_effect=lambda:clock[0]):self.store.refresh(self.a['workspace_id'],None,self.authorize,fetch)
        with self.store.request(False,self.a['workspace_id']) as store:
            self.assertEqual(store.db.execute('SELECT sync_generation FROM catproof_connections WHERE id=?',(deferred,)).fetchone()[0],'another-inflight-sync')

    def test_queued_source_changed_before_fetch_is_skipped(self):
        with self.store.request(True,self.a['workspace_id']) as store:
            first=store.add_connection({'name':'First','kind':'discovery','url':'https://first.invalid'})
            queued=store.add_connection({'name':'Queued','kind':'discovery','url':'https://queued.invalid'})
            store.db.execute("UPDATE catproof_connections SET last_attempt='2026-01-01' WHERE id=?",(queued,))
        called=[]
        def fetch(job,env,timeout):
            called.append(job['id'])
            if job['id']==first:
                with self.store.request(True,self.a['workspace_id']) as store:
                    store.set_enabled(queued,False);store.set_enabled(queued,True)
            return {'agents':[],'assets':[]}
        self.store.refresh(self.a['workspace_id'],None,self.authorize,fetch)
        self.assertEqual(called,[first])

class PostgreSQLReadTests(unittest.TestCase):
    def test_maximum_entra_scope_can_be_saved_and_duplicates_rejected(self):
        import uuid
        from federation import DuplicateConnection
        store=registry();self.addCleanup(store.db.close)
        body={'name':'Entra','kind':'entra','url':'https://graph.microsoft.com','credential_prefix':'ENTRA',
              'config':{'principal_ids':','.join(str(uuid.uuid4()) for _ in range(100))}}
        cid=store.add_connection(body)
        self.assertEqual(store.snapshot()['sources'][0]['id'],cid)
        with self.assertRaises(DuplicateConnection):store.add_connection(body)
        from psycopg.errors import UniqueViolation
        with self.assertRaises(UniqueViolation),store.db:
            store.db.execute("INSERT INTO catproof_connections(id,name,kind,url,credential_prefix,source_filter,config,created_at) SELECT 'duplicate',name,kind,url,credential_prefix,source_filter,config,created_at FROM catproof_connections WHERE id=?",(cid,))

    def test_asset_links_use_one_query_for_all_assets(self):
        store=registry();self.addCleanup(store.db.close)
        cid=store.add_connection({'name':'Tools','kind':'mcp','url':'https://registry.invalid'})
        assets=[dict(id=str(i),name='Tool '+str(i),source='MCP',resource_type='mcp_server',provenance='live',last_seen='2026-10-27T00:00:00Z') for i in range(12)]
        store.ingest(cid,{'agents':[],'assets':assets});eid=store.create({'name':'Agent'})
        for asset in store.snapshot()['assets']:store.link_asset(asset['asset_id'],eid)
        with patch.object(store.db,'execute',wraps=store.db.execute) as execute:
            snapshot=store.snapshot()
        queries=[call.args[0] for call in execute.call_args_list if 'FROM catproof_asset_links' in call.args[0]]
        self.assertEqual(len(queries),1)
        self.assertTrue(all(a['linked_agents']==[eid] for a in snapshot['assets']))
