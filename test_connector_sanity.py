"""Cross-connector contract audit: malformed reads cannot masquerade as empty inventory."""
import copy
import json
import unittest
from unittest.mock import patch
from adapters.catalog import CATALOG, BASE_CATALOG
from adapters.readers import read_connector, agent, asset
from adapters.transport import Response, continuation
from federation import validate_assets, discover
from registry import ConnectorError, databricks, servicenow
from test_connectors import FakeTransport
from tests.db import registry

# One minimal valid recorded shape for each non-AWS HTTP reader. Deliberately no
# customer credentials, external network, runtime calls or invented business owners.
CASES={
 'snowflake':({'database':'DB','schema':'PUBLIC'},[{'name':'agent','owner':'ROLE'}],'agents'),
 'copilot':({}, {'data':[{'id':'agent','properties':{'displayName':'Agent','ownerId':'owner-id'}}]},'agents'),
 'm365':({}, {'value':[{'id':'agent','displayName':'Agent','shortDescription':'Documented description','isBlocked':True,'supportedHosts':['Copilot']}]},'agents'),
 'foundry':({'project':'project'}, {'data':[{'id':'agent','name':'Agent'}],'has_more':False},'agents'),
 'google_registry':({'project':'p','location':'r'}, {'agents':[{'name':'projects/p/locations/r/agents/a'}]},'agents'),
 'google_runtime':({'project':'p','location':'r'}, {'reasoningEngines':[{'name':'projects/p/locations/r/reasoningEngines/a'}]},'agents'),
 'langgraph':({}, [{'assistant_id':'agent','name':'Agent'}],'agents'),
 'salesforce':({'api_version':'68.0'}, Response(None,text='<Envelope xmlns="http://schemas.xmlsoap.org/soap/envelope/" xmlns:m="http://soap.sforce.com/2006/04/metadata"><Body><m:listMetadataResponse><m:result><m:id>agent</m:id><m:fullName>Agent</m:fullName></m:result></m:listMetadataResponse></Body></Envelope>'),'agents'),
 'a2a':({}, {'name':'Agent','skills':[],'url':'https://agent.invalid/a2a'},'agents'),
 'entra':({'principal_ids':'principal'}, {'id':'principal','displayName':'Identity'},'assets'),
 'mcp':({}, {'servers':[{'server':{'name':'org/tool','version':'1'}}],'metadata':{}},'assets'),
 'lovable':({'workspace_id':'workspace'}, {'data':[{'id':'project','workspace_id':'workspace','is_published':False,'name':'Project'}],'pagination':{'has_more':False,'next_cursor':None}},'assets'),
}

class ConnectorSanityTests(unittest.TestCase):
    def read(self,kind,response=None,extra=None):
        config,fixture,_=CASES[kind]
        responses=[copy.deepcopy(fixture if response is None else response)]
        if kind=='entra':responses.append({'value':[]})
        if extra:responses.extend(extra)
        t=FakeTransport(responses,origin='https://api.lovable.dev' if kind=='lovable' else 'https://fixture.invalid')
        connection={'kind':kind,'url':t.origin,'credential_prefix':'SANITY','config':config}
        return read_connector(connection,{'SANITY_TOKEN':'fictional-test-only'},transport_factory=lambda *args:t)

    def test_every_catalog_connector_is_accounted_for(self):
        self.assertEqual(set(CATALOG),set(CASES)|{'aws_bedrock','aws_agentcore','aws_registry'})
        self.assertEqual(set(BASE_CATALOG),{'databricks','servicenow','atlas','discovery','local'})

    def test_http_contracts_classify_inventory_and_keep_secrets_out(self):
        for kind,(_,_,category) in CASES.items():
            with self.subTest(kind=kind):
                result=self.read(kind)
                self.assertEqual(len(result[category]),1)
                self.assertEqual(result['assets' if category=='agents' else 'agents'],[])
                self.assertNotIn('fictional-test-only',json.dumps(result))
        m365=self.read('m365')['agents'][0]
        self.assertEqual(m365['description'],'Documented description')
        self.assertTrue(m365['evidence']['isBlocked'])
        self.assertEqual(m365['owner'],'Unassigned');self.assertEqual(m365['status'],'unknown')

    def test_google_registry_uses_documented_card_and_skills_fields(self):
        row={'name':'projects/p/locations/r/agents/a','agentId':'stable-agent','card':{'type':'A2A_AGENT_CARD','content':{'name':'Card name'}},'skills':[{'id':'lookup','name':'Lookup'}],'protocols':[{'type':'A2A_AGENT'}]}
        saved=self.read('google_registry',{'agents':[row]})['agents'][0]
        for key in ('agentId','card','skills','protocols'):self.assertEqual(saved['evidence'][key],row[key])
        self.assertNotIn('agentCard',saved['evidence'])

    def test_microsoft_packages_need_explicit_agent_evidence(self):
        result=self.read('m365',{'value':[{'id':'app','displayName':'Word add-in','supportedHosts':['Word']},{'id':'bot','elementTypes':['bot']},{'id':'unknown','displayName':'Unclassified package'}]})
        self.assertEqual([a['source_id'] for a in result['agents']],['bot'])
        self.assertEqual([a['source_id'] for a in result['assets']],['app','unknown'])

    def test_all_http_readers_reject_error_envelopes_not_empty_success(self):
        for kind in CASES:
            with self.subTest(kind=kind),self.assertRaises(ConnectorError):
                self.read(kind,{'error':{'message':'fictional failure'}})

    def test_valid_empty_inventory_is_distinct_from_invalid_response(self):
        empties={'snowflake':[],'copilot':{'data':[]},'m365':{'value':[]},'foundry':{'data':[],'has_more':False},'google_registry':{},'google_runtime':{},'langgraph':[],'mcp':{'servers':[],'metadata':{}},'lovable':{'data':[],'pagination':{'has_more':False,'next_cursor':None}}}
        for kind,empty in empties.items():
            with self.subTest(kind=kind):
                result=self.read(kind,empty);self.assertEqual(result['agents'],[]);self.assertEqual(result['assets'],[])

    def test_snowflake_short_page_malformed_next_link_is_not_complete(self):
        for link in ('<missing-close;rel="next"','<https://fixture.invalid/a>;rel="next", <https://fixture.invalid/b>;rel="next"'):
            with self.subTest(link=link),self.assertRaises(ConnectorError):self.read('snowflake',Response([],{'Link':link}))
        result=self.read('snowflake',Response([],{'Link':"<https://fixture.invalid/next>;rel='next'"}),extra=[[]])
        self.assertEqual(result['agents'],[])

    def test_identity_and_owner_objects_cannot_be_stringified_into_records(self):
        for factory,args in [(agent,('Source','Agent')), (asset,('Source','tool'))]:
            for invalid in (None,'','  ',False,123,{},[]):
                with self.subTest(factory=factory.__name__,invalid=invalid),self.assertRaises(ConnectorError):factory(*args,invalid)
        with self.assertRaises(ConnectorError):agent('Source','Agent','valid',owner={'id':'not-a-name'})

    def test_falsy_and_structured_cursors_are_not_end_of_inventory(self):
        for invalid in (False,0,[],{},123):
            for kind,key,where in [('copilot','skipToken',None),('google_registry','nextPageToken',None),('google_runtime','nextPageToken',None),('m365','@odata.nextLink',None),('mcp','nextCursor','metadata')]:
                response=copy.deepcopy(CASES[kind][1]);(response[where] if where else response)[key]=invalid
                with self.subTest(kind=kind,invalid=invalid),self.assertRaises(ConnectorError):self.read(kind,response)
        for value in (None,''):
            self.assertIsNone(continuation({'token':value},'token'))

    def test_foundry_requires_explicit_boolean_completion_and_cursor(self):
        for flag in ('false',0,None):
            with self.subTest(flag=flag),self.assertRaises(ConnectorError):self.read('foundry',{'data':[],'has_more':flag})
        with self.assertRaises(ConnectorError):self.read('foundry',{'data':[]})
        with self.assertRaises(ConnectorError):self.read('foundry',{'data':[],'has_more':True,'last_id':0})

    def test_partial_error_envelopes_and_ambiguous_tokens_are_rejected(self):
        for kind,key in [('copilot','data'),('foundry','data'),('m365','value'),('google_registry','agents'),('mcp','servers'),('a2a','card'),('entra','principal')]:
            response=copy.deepcopy(CASES[kind][1]);response['error']={'code':'partial'}
            with self.subTest(kind=kind),self.assertRaises(ConnectorError):self.read(kind,response)
        for patch_value in ({'skipToken':'a','$skipToken':'b'},{'totalRecords':False},{'resultTruncated':'maybe'}):
            response={**CASES['copilot'][1],**patch_value}
            with self.subTest(patch=patch_value),self.assertRaises(ConnectorError):self.read('copilot',response)

    def test_entra_must_return_the_requested_principal(self):
        with self.assertRaises(ConnectorError):self.read('entra',{'id':'different-principal'})

    def test_mcp_requires_version_identity_and_valid_metadata(self):
        for response in ({'servers':[{'server':{'name':'org/tool'}}],'metadata':{}},{'servers':[],'metadata':False}):
            with self.subTest(response=response),self.assertRaises(ConnectorError):self.read('mcp',response)

    def test_databricks_and_servicenow_reject_partial_errors(self):
        with self.assertRaises(ConnectorError):databricks({'DATABRICKS_HOST':'https://fixture.invalid','DATABRICKS_TOKEN':'fictional'},lambda *args:{'knowledge_assistants':[],'error':'partial'})
        with self.assertRaises(ConnectorError):databricks({'DATABRICKS_HOST':'https://fixture.invalid','DATABRICKS_TOKEN':'fictional'},lambda *args:{'knowledge_assistants':[],'next_page_token':False})
        with self.assertRaises(ConnectorError):servicenow({'SERVICENOW_INSTANCE_URL':'https://fixture.invalid','SERVICENOW_TOKEN':'fictional'},lambda *args:{'result':[],'error':'partial'})

    def test_peer_and_discovery_reject_partial_error_envelopes(self):
        class ResponseBody:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,*args):return json.dumps({'schema_version':'atlas.registry.v1','agents':[],'error':'partial'}).encode()
        class Opener:
            def open(self,*args,**kwargs):return ResponseBody()
        for kind in ('atlas','discovery'):
            with self.subTest(kind=kind),patch('federation.build_opener',return_value=Opener()),self.assertRaises(ValueError):
                discover({'kind':kind,'url':'https://fixture.invalid','credential_prefix':'','source_filter':''},{})

    def test_asset_validation_matches_import_and_preview_contract(self):
        good=dict(id='tool',name='Tool',source='MCP',resource_type='mcp_server',provenance='live',last_seen='2026-10-29T12:00:00Z')
        for changes in ({'provenance':'invented'},{'present':'false'},{'stale':0},{'id':' '}, {'name':{}}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):validate_assets([{**good,**changes}])
        with self.assertRaises(ValueError):validate_assets([good,good])

class AWSContractSanityTests(unittest.TestCase):
    def test_all_aws_readers_validate_calls_against_installed_sdk_and_follow_pages(self):
        import botocore.session
        from botocore.validate import validate_parameters
        models=botocore.session.get_session()
        cases=[('aws_bedrock','bedrock-agent','ListAgents','list_agents','agentSummaries',{'agentId':'agent','agentName':'Agent'},{}),('aws_agentcore','bedrock-agentcore-control','ListAgentRuntimes','list_agent_runtimes','agentRuntimes',{'agentRuntimeArn':'arn:runtime:agent','agentRuntimeName':'Agent'},{}),('aws_registry','agent-registry-control','ListRegistryRecords','list_registry_records','registryRecords',{'recordId':'agent','recordType':'AGENT','name':'Agent'},{'registry_id':'registry123456'})]
        for kind,service,operation,method,key,row,config in cases:
            with self.subTest(kind=kind):
                model=models.get_service_model(service).operation_model(operation);calls=[]
                class Client:pass
                client=Client()
                def call(**params):
                    validate_parameters(params,model.input_shape);calls.append(dict(params))
                    return {key:[row],'nextToken':'page2'} if len(calls)==1 else {key:[]}
                setattr(client,method,call)
                result=read_connector({'kind':kind,'credential_prefix':'AWS','url':'https://fixture.invalid','config':{'region':'us-east-1',**config}},{},aws_factory=lambda *args:client)
                self.assertEqual(len(result['agents']),1);self.assertEqual(calls[1]['nextToken'],'page2')
                def bad(**params):return {key:[],'nextToken':False}
                setattr(client,method,bad)
                with self.assertRaises(ConnectorError):read_connector({'kind':kind,'credential_prefix':'AWS','url':'https://fixture.invalid','config':{'region':'us-east-1',**config}},{},aws_factory=lambda *args:client)

class ConnectorFailurePreservationTests(unittest.TestCase):
    def test_asset_provenance_cannot_overwrite_existing_evidence_and_links(self):
        store=registry();self.addCleanup(store.db.close)
        cid=store.add_connection({'name':'MCP','kind':'mcp','url':'https://fixture.invalid'})
        row=asset('MCP','mcp_server','org/tool@1','Original')
        store.ingest(cid,{'agents':[],'assets':[row]})
        before=store.snapshot();changed={**row,'provenance':'seeded','name':'Fictional replacement'}
        with self.assertRaises(ValueError):store.ingest(cid,{'agents':[],'assets':[changed]})
        after=store.snapshot();self.assertEqual(after['assets'],before['assets']);self.assertEqual(after['events'],before['events'])
        store.ingest(cid,{'agents':[],'assets':[{**row,'present':False,'stale':True}]})
        saved=store.snapshot()['assets'][0];self.assertFalse(saved['present']);self.assertTrue(saved['stale'])

    def test_malformed_later_page_retains_agents_and_audit_reports_failure(self):
        from test_federation import payload, record
        store=registry();self.addCleanup(store.db.close)
        cid=store.add_connection({'name':'Foundry','kind':'foundry','url':'https://fixture.invalid','credential_prefix':'F','config':{'project':'project'}})
        store.ingest(cid,payload(record()))
        original=store.snapshot()['agents'][0]['registry_id']
        def reader(*args):
            fake=FakeTransport([{'data':[{'id':'replacement','name':'Replacement'}],'has_more':True,'last_id':'replacement'},{'data':[],'has_more':'false'}])
            return read_connector({'kind':'foundry','url':fake.origin,'credential_prefix':'F','config':{'project':'project'}},{'F_TOKEN':'fictional'},transport_factory=lambda *args:fake)
        store.reader=reader;result=store.refresh(cid)
        self.assertEqual([a['registry_id'] for a in result['agents']],[original])
        self.assertTrue(result['observations'][0]['present']);self.assertTrue(result['observations'][0]['stale'])
        self.assertEqual(result['runs'][0]['outcome'],'failed')
