from tests.db import registry as FederatedRegistry
"""Recorded API-shape fixtures; no vendor credentials or production network requests."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from adapters import CATALOG,validate_config,read_connector
from adapters.transport import Response,Transport
from registry import ConnectorError

class FakeTransport:
    def __init__(self,responses,origin='https://example.invalid'):
        self.responses=list(responses);self.calls=[];self.origin=origin
    def request(self,path,params=None,body=None,method='GET',headers=None,xml=False):
        self.calls.append(dict(path=path,params=params,body=body,method=method,headers=headers or {},xml=xml))
        if not self.responses:raise AssertionError('Unexpected extra request')
        result=self.responses.pop(0)
        return result if isinstance(result,Response) else Response(result)

class ConnectorTests(unittest.TestCase):
    def read(self,kind,responses,config=None):
        fake=FakeTransport(responses);seen={}
        def factory(origin,headers):seen.update(headers);return fake
        result=read_connector({'kind':kind,'url':fake.origin,'credential_prefix':'TEST','config':config or {}},{'TEST_TOKEN':'private-test-token'},transport_factory=factory)
        return result,fake,seen
    def test_snowflake_role_and_qualified_identity(self):
        result,t,h=self.read('snowflake',[[{'name':'A','owner':'DATA_ROLE','profile':{'display_name':'Agent A'}}]],{'database':'DB','schema':'PUBLIC'})
        a=result['agents'][0];self.assertEqual(a['owner'],'DATA_ROLE');self.assertEqual(a['status'],'unknown')
        self.assertEqual(a['source_id'],'["DB","PUBLIC","A"]');self.assertIn('/databases/DB/schemas/PUBLIC/agents',t.calls[0]['path'])
    def test_copilot_delegated_query_pagination_and_last_page(self):
        responses=[{'data':[{'id':'1','properties':{'displayName':'Copilot'}}],'skipToken':'next','totalRecords':2}, {'data':[{'id':'2','properties':{}}],'totalRecords':2,'resultTruncated':0}]
        result,t,h=self.read('copilot',responses)
        self.assertEqual(len(result['agents']),2);self.assertEqual(t.calls[1]['body']['Options']['SkipToken'],'next')
        self.assertEqual(t.calls[0]['method'],'POST');self.assertEqual(t.calls[0]['body']['TableName'],'PowerPlatformResources')
    def test_copilot_uses_current_owner_not_creator(self):
        result,_,_=self.read('copilot',[{'data':[{'id':'1','properties':{'ownerId':'current-owner-id','createdBy':'creator-id'}},{'id':'2','properties':{'createdBy':'creator-only'}}]}])
        first,second=result['agents']
        self.assertEqual(first['owner'],'current-owner-id')
        self.assertIn('Entra object ID',first['evidence']['owner_type'])
        self.assertEqual(second['owner'],'Unassigned')
        with self.assertRaises(ConnectorError):self.read('copilot',[{'data':[{'id':'1','properties':{'ownerId':{'unexpected':'object'}}}]}])

    def test_copilot_truncation_fails(self):
        with self.assertRaises(ConnectorError):self.read('copilot',[{'data':[],'resultTruncated':1}])
    def test_m365_odata_pages(self):
        result,t,h=self.read('m365',[{'value':[{'id':'1','displayName':'Catalog agent','elementTypes':['declarativeAgent']}],'@odata.nextLink':'/beta/copilot/admin/catalog/packages?$skiptoken=page2'},{'value':[]}])
        self.assertEqual(len(t.calls),2);self.assertEqual(result['agents'][0]['source'],'Microsoft 365')
    def test_foundry_project_and_pagination(self):
        result,t,h=self.read('foundry',[{'data':[{'id':'a','name':'first'}],'has_more':True,'last_id':'a'},{'data':[{'id':'b','name':'second'}],'has_more':False}],{'project':'team-a'})
        self.assertEqual(len(result['agents']),2);self.assertEqual(t.calls[0]['path'],'/api/projects/team-a/agents')
        self.assertEqual(t.calls[1]['params']['after'],'a')
    def test_google_registry_and_runtime_are_distinct(self):
        for kind,key in [('google_registry','agents'),('google_runtime','reasoningEngines')]:
            with self.subTest(kind=kind):
                result,t,h=self.read(kind,[{key:[{'name':'projects/p/locations/r/'+key+'/a','displayName':'Google agent'}],'nextPageToken':'next'},{key:[]}],{'project':'p','location':'r'})
                self.assertEqual(len(result['agents']),1);self.assertEqual(t.calls[1]['params']['pageToken'],'next')
    def test_langgraph_does_not_execute_agent(self):
        result,t,h=self.read('langgraph',[[{'assistant_id':'a','graph_id':'g','name':'Assistant'}]])
        self.assertEqual(t.calls[0]['path'],'/assistants/search');self.assertEqual(t.calls[0]['method'],'POST')
        self.assertEqual(h,{'x-api-key':'private-test-token'});self.assertEqual(result['agents'][0]['kind'],'Assistant configuration')
    def test_salesforce_v68_read_only_metadata(self):
        xml='<Envelope xmlns="http://schemas.xmlsoap.org/soap/envelope/" xmlns:m="http://soap.sforce.com/2006/04/metadata"><Body><m:listMetadataResponse><m:result><m:id>A</m:id><m:fullName>Service_Agent</m:fullName></m:result></m:listMetadataResponse></Body></Envelope>'
        result,t,h=self.read('salesforce',[Response(None,text=xml)],{'api_version':'68.0'})
        self.assertEqual(result['agents'][0]['name'],'Service_Agent');self.assertIn('<m:listMetadata>',t.calls[0]['body']);self.assertNotIn('deploy',t.calls[0]['body'])
        self.assertNotIn('private-test-token',json.dumps(result))
    def test_salesforce_rejects_legacy_version(self):
        with self.assertRaises(ConnectorError):self.read('salesforce',[],{'api_version':'67.0'})
    def test_a2a_v1_advertisements_no_invocation(self):
        card={'name':'Advertised agent','skills':[{'id':'lookup','name':'Lookup'}],'version':'1','supportedInterfaces':[{'url':'https://agent.invalid/a2a','protocolVersion':'1.0'}]}
        result,t,h=self.read('a2a',[card]);self.assertEqual(len(result['agents']),1);self.assertEqual(t.calls[0]['path'],'/.well-known/agent-card.json')
        self.assertEqual(result['agents'][0]['status'],'unknown');self.assertIn('Not verified',result['agents'][0]['evidence']['signature_status'])
    def test_a2a_no_endpoint_is_not_an_agent(self):
        with self.assertRaises(ConnectorError):self.read('a2a',[{'name':'x','skills':[]}])
    def test_entra_is_evidence_not_new_agents(self):
        result,t,h=self.read('entra',[{'id':'principal-1','displayName':'Identity','appId':'app-1'},{'value':[{'id':'owner-1','displayName':'Source owner'}]}],{'principal_ids':'principal-1'})
        self.assertEqual(result['agents'],[]);self.assertEqual(result['assets'][0]['resource_type'],'identity')
        self.assertEqual(result['assets'][0]['evidence']['directory_owners'][0]['id'],'owner-1')
    def test_mcp_paginates_and_never_creates_agents(self):
        result,t,h=self.read('mcp',[{'servers':[{'server':{'name':'org/server','version':'1.0','description':'Tools'}}],'metadata':{'nextCursor':'next'}},{'servers':[],'metadata':{}}],{'search':'server'})
        self.assertEqual(result['agents'],[]);self.assertEqual(result['assets'][0]['resource_type'],'mcp_server');self.assertEqual(t.calls[1]['params']['cursor'],'next')
    def test_required_fields_and_credentials(self):
        with self.assertRaises(ValueError):validate_config('snowflake',{'database':'DB'})
        with self.assertRaises(ValueError):validate_config('a2a',{'token':'do-not-save'})
        with self.assertRaises(ConnectorError):read_connector({'kind':'m365','url':'https://graph.microsoft.com','credential_prefix':'M365','config':{}},{})
    def test_all_catalog_entries_have_coverage_and_reference(self):
        self.assertEqual(len(CATALOG),15)
        for kind,spec in CATALOG.items():self.assertTrue(spec['coverage'] and spec['reference'].startswith('https://'))

class AWSTests(unittest.TestCase):
    def read(self,kind,method,response,config):
        class Client:pass
        client=Client();calls=[]
        def call(**kwargs):calls.append(kwargs);return response
        setattr(client,method,call);services=[]
        def factory(service,region,prefix,env):services.append(service);return client
        result=read_connector({'kind':kind,'credential_prefix':'AWS_TEST','url':'https://aws.invalid','config':config},{},aws_factory=factory)
        return result,services,calls
    def test_bedrock(self):
        result,services,calls=self.read('aws_bedrock','list_agents',{'agentSummaries':[{'agentId':'a','agentName':'Bedrock agent','agentStatus':'PREPARED'}]}, {'region':'us-east-1'})
        self.assertEqual(services,['bedrock-agent']);self.assertEqual(len(result['agents']),1)
    def test_agentcore(self):
        result,services,calls=self.read('aws_agentcore','list_agent_runtimes',{'agentRuntimes':[{'agentRuntimeArn':'arn:runtime:a','agentRuntimeName':'Runtime','status':'READY'}]}, {'region':'us-east-1'})
        self.assertEqual(result['agents'][0]['status'],'active');self.assertEqual(services,['bedrock-agentcore-control'])
    def test_registry_uses_current_namespace_and_separates_tools(self):
        response={'registryRecords':[{'recordId':'a','name':'Agent','recordType':'AGENT','status':'DRAFT'},{'recordId':'m','name':'Tools','recordType':'MCP','status':'APPROVED'}]}
        result,services,calls=self.read('aws_registry','list_registry_records',response,{'region':'us-east-1','registry_id':'registry1'})
        self.assertEqual(services,['agent-registry-control']);self.assertEqual(len(result['agents']),1);self.assertEqual(len(result['assets']),1)
        self.assertEqual(result['agents'][0]['status'],'unknown');self.assertEqual(calls[0]['registryId'],'registry1')
    def test_sdk_installed_operation_models(self):
        try:import botocore.session
        except ImportError:self.skipTest('Run with requirements.txt installed to validate SDK models')
        session=botocore.session.get_session()
        for service,operation in [('agent-registry-control','ListRegistryRecords'),('bedrock-agent','ListAgents'),('bedrock-agentcore-control','ListAgentRuntimes')]:
            self.assertIn(operation,session.get_service_model(service).operation_names)

class TransportTests(unittest.TestCase):
    def test_cross_origin_page_and_redirect_rejected(self):
        t=Transport('https://good.invalid',{'Authorization':'Bearer secret'})
        with self.assertRaises(ConnectorError):t.request('https://other.invalid/list')
        with self.assertRaises(ConnectorError):t.request('//other.invalid/list')
    def test_timeout_error_does_not_expose_token(self):
        with patch('adapters.transport.build_opener') as opener:
            opener.return_value.open.side_effect=RuntimeError('secret')
            with self.assertRaises(ConnectorError) as error:Transport('https://good.invalid',{'Authorization':'Bearer secret'}).request('/list')
            self.assertNotIn('secret',str(error.exception))

class AssetStoreTests(unittest.TestCase):
    def test_assets_persist_and_link_without_creating_agents(self):
        with tempfile.TemporaryDirectory() as folder:
            s=FederatedRegistry(Path(folder)/'db',env={});cid=s.add_connection({'name':'MCP','kind':'mcp','url':'https://registry.invalid','config':{}})
            s.ingest(cid,{'agents':[],'assets':[{'id':'tool','name':'Tool','source':'MCP','resource_type':'mcp_server','provenance':'live','last_seen':'2026-10-27T00:00:00Z','evidence':{}}]})
            self.assertEqual(s.snapshot()['agents'],[]);aid=s.snapshot()['assets'][0]['asset_id'];eid=s.create({'name':'Business agent'})
            s.link_asset(aid,eid);self.assertEqual(len(s.snapshot()['agents'][0]['linked_assets']),1)
            s.set_enabled(cid,False);self.assertTrue(s.snapshot()['assets'][0]['stale'])
            s.db.close();reopened=FederatedRegistry(Path(folder)/'db',env={});self.assertEqual(len(reopened.snapshot()['agents'][0]['linked_assets']),1);reopened.db.close()
    def test_disabled_agent_evidence_excluded_from_catalog_and_export(self):
        from test_federation import payload,record
        with tempfile.TemporaryDirectory() as folder:
            s=FederatedRegistry(Path(folder)/'db',env={});cid=s.add_connection({'name':'Paused source','kind':'discovery','url':'https://source.invalid'})
            s.ingest(cid,payload(record(name='Private agent')));s.set_enabled(cid,False)
            self.assertEqual(s.catalog()['agents'],[]);self.assertNotIn('Private agent',s.export().decode())
            self.assertFalse(s.snapshot()['agents'][0]['source_enabled']);s.db.close()
    def test_source_capability_changes_are_audited(self):
        from test_federation import payload,record
        with tempfile.TemporaryDirectory() as folder:
            s=FederatedRegistry(Path(folder)/'db',env={});cid=s.add_connection({'name':'Cards','kind':'discovery','url':'https://source.invalid'})
            a=record();a['evidence']={'skills':['read']};s.ingest(cid,payload(a))
            a['evidence']={'skills':['read','write']};s.ingest(cid,payload(a))
            changes=[e for e in s.snapshot()['events'] if e['kind']=='source_changed']
            self.assertIn('evidence',changes[0]['detail']);self.assertEqual(s.snapshot()['agents'][0]['lifecycle'],'draft');s.db.close()
    def test_related_asset_changes_preserve_links(self):
        with tempfile.TemporaryDirectory() as folder:
            s=FederatedRegistry(Path(folder)/'db',env={});cid=s.add_connection({'name':'MCP','kind':'mcp','url':'https://source.invalid'})
            a={'id':'tool','name':'Tool','source':'MCP','resource_type':'mcp_server','provenance':'live','last_seen':'2026-10-27T00:00:00Z','evidence':{'version':'1'}}
            s.ingest(cid,{'agents':[],'assets':[a]});aid=s.snapshot()['assets'][0]['asset_id'];eid=s.create({'name':'Agent'});s.link_asset(aid,eid)
            a['evidence']={'version':'2'};s.ingest(cid,{'agents':[],'assets':[a]})
            self.assertTrue(any(e['kind']=='asset_changed' for e in s.snapshot()['events']));self.assertEqual(len(s.snapshot()['agents'][0]['linked_assets']),1);s.db.close()
