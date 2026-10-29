"""Machine-readable configuration, credential and coverage contracts."""
def spec(label,category,fields,auth,coverage,reference):
    return dict(label=label,category=category,fields=[dict(key=k,label=l,required=r,example=e) for k,l,r,e in fields],auth=auth,coverage=coverage,reference=reference,verification='contract-tested; tenant access required')
CATALOG={
 'lovable':spec('Lovable Projects','applications',[('workspace_id','Lovable workspace ID',True,'workspace_your-id')],'Workspace API key entered securely in CatProof; Workspace: Read','One authorized Business/Enterprise workspace. Projects are application assets, not automatically agents. Publication does not prove runtime health.','https://docs.lovable.dev/api-reference/projects/list-projects'),
 'snowflake':spec('Snowflake Cortex Agents','agents',[('database','Database',True,'ANALYTICS'),('schema','Schema',True,'PUBLIC'),('role','Active role (optional)',False,'CATPROOF_READER')],'PREFIX_TOKEN (PAT or OAuth bearer)','One configured database/schema per connection; source owner is a Snowflake role.','https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents-rest-api'),
 'copilot':spec('Microsoft Copilot Studio / Agent Builder','agents',[],'PREFIX_TOKEN (delegated Power Platform token; app-only is not supported)','Tenant inventory visible to the delegated user; does not cover Foundry or original V1 Power Virtual Agents.','https://learn.microsoft.com/en-us/power-platform/admin/inventory-api'),
 'm365':spec('Microsoft 365 agent catalog','agents',[],'PREFIX_TOKEN (Microsoft Graph token with catalog read access; Agent 365 license required)','Catalog packages with explicit agent elements or a Copilot host become agents; other packages become application assets. Separate from Copilot Studio inventory.','https://learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/api/admin-settings/package/overview'),
 'foundry':spec('Microsoft Foundry','agents',[('project','Project name',True,'my-project')],'PREFIX_TOKEN (Entra token for https://ai.azure.com/)','Configured Foundry project, v1 agent definitions; not every Azure resource or classic Assistants deployment.','https://learn.microsoft.com/en-us/rest/api/microsoft-foundry/aiproject'),
 'aws_bedrock':spec('AWS Bedrock Agents','agents',[('region','AWS region',True,'us-east-1')],'PREFIX_PROFILE or PREFIX_ACCESS_KEY_ID + PREFIX_SECRET_ACCESS_KEY (+ PREFIX_SESSION_TOKEN)','One AWS account/region; lists definitions, not an assertion that aliases are serving.','https://docs.aws.amazon.com/bedrock/latest/APIReference/API_agent_ListAgents.html'),
 'aws_agentcore':spec('AWS AgentCore Runtime','agents',[('region','AWS region',True,'us-east-1')],'AWS profile or temporary access-key environment references','One AWS account/region; runtime identity is kept separate from agent definition identity.','https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_ListAgentRuntimes.html'),
 'aws_registry':spec('AWS Agent Registry','registry',[('region','AWS region',True,'us-east-1'),('registry_id','Registry ID or ARN',True,'your-registry-id')],'AWS profile or temporary access-key environment references','Current agent-registry-control namespace; control-plane records, including unapproved states. Non-agent records become assets.','https://docs.aws.amazon.com/cli/latest/reference/agent-registry-control/list-registry-records.html'),
 'google_registry':spec('Google Agent Registry','registry',[('project','Project ID',True,'my-project'),('location','Location',True,'us-central1'),('billing_project','Quota/billing project (optional)',False,'my-project')],'PREFIX_TOKEN (Google OAuth access token)','Configured project/location; not organization-wide discovery.','https://docs.cloud.google.com/agent-registry/reference/rest/v1/projects.locations.agents/list'),
 'google_runtime':spec('Google Agent Runtime / Agent Engine','agents',[('project','Project ID',True,'my-project'),('location','Location',True,'us-central1'),('billing_project','Quota/billing project (optional)',False,'my-project')],'PREFIX_TOKEN (Google OAuth access token)','Configured project/location reasoningEngines; native runtime identity stays separate from registry identity.','https://docs.cloud.google.com/gemini-enterprise-agent-platform/reference/rest/v1/projects.locations.reasoningEngines/list'),
 'langgraph':spec('LangSmith / LangGraph Agent Server','agents',[],'PREFIX_TOKEN (LangSmith API key)','One configured deployment endpoint; assistants are configurations, not automatically distinct business agents.','https://github.com/langchain-ai/docs/blob/main/src/langsmith/agent-server-openapi.json'),
 'salesforce':spec('Salesforce Agentforce','agents',[('api_version','Metadata API version',True,'68.0')],'PREFIX_TOKEN (Salesforce OAuth access token)','v68+ AiAgentDefinition metadata via read-only listMetadata; excludes Einstein Bots. Refuses saturated 1,000-record responses.','https://developer.salesforce.com/docs/ai/agentforce/references/agents-metadata-tooling'),
 'a2a':spec('A2A Agent Card','agents',[('card_path','Agent Card path',False,'/.well-known/agent-card.json')],'Optional PREFIX_TOKEN for protected cards','One supplied Agent Card endpoint; advertised capabilities only, no invocation or inferred business owner.','https://a2a-protocol.org/dev/specification/agent-card/'),
 'entra':spec('Microsoft Entra identities','identity',[('principal_ids','Service principal object IDs (comma-separated)',True,'object-id-1,object-id-2')],'PREFIX_TOKEN (Microsoft Graph; Application.Read.All)','Explicit principal IDs plus directory owners. Identity evidence is stored as assets, not agents.','https://learn.microsoft.com/graph/api/serviceprincipal-list-owners?view=graph-rest-1.0'),
 'mcp':spec('MCP Registry','tools',[('search','Server name filter',False,'filesystem')],'Optional PREFIX_TOKEN for a private registry','MCP Registry v0.1; servers are tool assets, not agents. No packages are installed or executed.','https://github.com/modelcontextprotocol/registry/blob/main/docs/reference/api/official-registry-api.md'),
}
for kind in ('a2a','mcp'):
    CATALOG[kind]['verification']='fixture-tested; verify the configured endpoint'

# Required when creating a connection; older saved connections remain readable.
ACCOUNT_FIELDS={'copilot':'tenant_id','m365':'tenant_id','aws_bedrock':'account_id','aws_agentcore':'account_id'}
for kind,key in ACCOUNT_FIELDS.items():
    CATALOG[kind]['fields'].append(dict(key=key,label='Tenant ID' if key=='tenant_id' else 'AWS account ID',required=False,required_on_create=True,example='00000000-0000-0000-0000-000000000001' if key=='tenant_id' else '123456789012'))


def validate_config(kind,config):
    if not isinstance(config,dict):raise ValueError('Connector configuration must be an object')
    if kind not in CATALOG:
        if config:raise ValueError('This adapter takes no additional configuration')
        return {}
    allowed={f['key']:f for f in CATALOG[kind]['fields']}
    if set(config)-set(allowed):raise ValueError('Unknown connector configuration field')
    for key,f in allowed.items():
        value=config.get(key,'')
        if not isinstance(value,str) or len(value)>4000 or '\r' in value or '\n' in value:raise ValueError('Invalid connector configuration value')
        if f['required'] and not value.strip():raise ValueError('Required connector field: '+f['label'])
    return {k:v.strip() for k,v in config.items()}

BASE_CATALOG={
 'local':spec('CatProof Local · Codex & Claude Code','devices',[],'Owner-issued one-time enrollment; revocable report-only device token','Opt-in macOS/Linux collector for installed clients, configured resource names and lifecycle observations. No prompts, code or transcripts. Sessions are not canonical agents.','https://learn.chatgpt.com/docs/hooks'),
 'databricks':spec('Databricks Agent Bricks','agents',[],'PREFIX_TOKEN','Knowledge Assistants and Supervisor Agent definitions in one configured workspace.','https://docs.databricks.com/api/knowledge-assistants/v1/knowledge-assistant'),
 'servicenow':spec('ServiceNow AI Agent Studio','agents',[],'PREFIX_TOKEN or PREFIX_USERNAME + PREFIX_PASSWORD','One configured AI Agent Studio instance. Requires authorized network access and table/field read permissions.','https://servicenow.github.io/sdk/api/aiagent-api'),
 'atlas':spec('CatProof peer registry','registry',[],'Optional PREFIX_TOKEN','Read-only single-hop canonical catalog federation. The atlas.registry.v1 wire contract is retained for compatibility.','/api/contract'),
 'discovery':spec('Normalized discovery registry','registry',[],'Optional PREFIX_TOKEN','Read a normalized /api/inventory source. Loopback bridges may refresh locally; remote sources are GET-only.','/api/contract'),
}
for item in BASE_CATALOG.values():item['verification']='Existing read-only adapter; verify configured account access'

BASE_CATALOG['local']['verification']='Collector and ingestion tests; client hook delivery requires local setup and trust'

# Both configuration UI and saved-connection settings use the same public auth contract.
from .auth import auth_spec
for kind,item in {**CATALOG,**BASE_CATALOG}.items():
    item['authentication']=auth_spec(kind)
    if kind!='local':item['auth']='Enter credentials securely in CatProof. '+item['authentication']['hint']

for kind,item in {**CATALOG,**BASE_CATALOG}.items():
    item['supports_source_filter']=kind in ('atlas','discovery')
