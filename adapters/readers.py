"""Vendor enumeration only. No inference, invocation, deployment or vendor mutation."""
import json
import re
import xml.etree.ElementTree as ET
from urllib.parse import quote,urlencode,urlsplit
from xml.sax.saxutils import escape
from registry import ConnectorError,now
from .catalog import CATALOG,validate_config
from .transport import Transport,collection,pages,odata,continuation

def text(value,default=''):
    if value is None:return default
    if hasattr(value,'isoformat'):return value.isoformat()
    if isinstance(value,(dict,list,tuple,bool)):raise ConnectorError('Invalid structured value in source metadata; previous inventory retained.')
    return str(value)

def source_identifier(value):
    if not isinstance(value,str) or not value.strip():raise ConnectorError('Source record has no valid string identity; previous inventory retained.')
    return value

def agent(source,kind,identifier,name=None,owner=None,status=None,description=None,evidence=None):
    identifier_value=source_identifier(identifier)
    raw=text(status,'UNKNOWN');normalized={'ACTIVE':'active','READY':'active','ONLINE':'active','INACTIVE':'inactive','FAILED':'error','ERROR':'error','CREATING':'pending','UPDATING':'pending'}.get(raw.upper(),'unknown')
    return dict(id=source.lower()+':'+kind+':'+identifier_value,source_id=identifier_value,name=text(name) or identifier_value,owner=text(owner) or 'Unassigned',
                source=source,kind=kind,status=normalized,source_status=raw,description=text(description),provenance='live',last_seen=now(),evidence=evidence or {})

def asset(source,kind,identifier,name=None,description=None,evidence=None):
    identifier_value=source_identifier(identifier)
    return dict(id=source.lower()+':'+kind+':'+identifier_value,source_id=identifier_value,name=text(name) or identifier_value,source=source,
                resource_type=kind,description=text(description),provenance='live',last_seen=now(),evidence=evidence or {})

def token(env,prefix,required=True):
    value=env.get(prefix+'_TOKEN') if prefix else None
    if required and not value:raise ConnectorError('Access token missing. Open this connection and choose Configure credentials.')
    return value

def snowflake(t,c):
    path='/api/v2/databases/'+quote(c['database'],safe='')+'/schemas/'+quote(c['schema'],safe='')+'/agents'
    rows=[];params={'showLimit':100};seen=set();nextpath=path
    for _ in range(100):
        response=t.request(nextpath,params);data=response.data
        if not isinstance(data,list):raise ConnectorError('Expected a Snowflake agent array.')
        for a in data:
            if not isinstance(a,dict) or not isinstance(a.get('name'),str) or not a['name'].strip():raise ConnectorError('Invalid Snowflake agent record.')
            ident=json.dumps([c['database'],c['schema'],a['name']],separators=(',',':'))
            if ident in seen:raise ConnectorError('Repeated Snowflake agent/page.')
            seen.add(ident);rows.append(agent('Snowflake','Cortex Agent',ident,(a.get('profile') or {}).get('display_name') or a['name'],a.get('owner'),description=a.get('comment'),evidence={'database':c['database'],'schema':c['schema'],'created_on':a.get('created_on'),'owner_type':'Snowflake role'}))
        link=response.headers.get('Link') or response.headers.get('link','')
        links=re.findall(r'<([^>]+)>;\s*rel=["\']?next["\']?(?=\s*(?:,|$))',link)
        if len(links)>1 or (not links and re.search(r'rel\s*=\s*["\']?next',link)):raise ConnectorError('Malformed or ambiguous Snowflake next-page link.')
        if links:nextpath=links[0];params={};continue
        if len(data)<100:return rows,[]
        params={'showLimit':100,'fromName':data[-1]['name']};nextpath=path
    raise ConnectorError('Snowflake pagination limit reached.')

def copilot(t,c):
    rows=[];skip='';seen=set()
    for _ in range(100):
        body={'TableName':'PowerPlatformResources','Clauses':[{'$type':'where','FieldName':'type','Operator':'==','Values':["'microsoft.copilotstudio/agents'"]}], 'Options':{'Top':1000,'SkipToken':skip}}
        data=t.request('/resourcequery/resources/query',{'api-version':'2024-10-01'},body,'POST').data
        raw=collection(data,'data')
        for a in raw:
            p=a.get('properties') or {};ident=a.get('id') or a.get('name')
            if not isinstance(p,dict):raise ConnectorError('Invalid Power Platform resource properties.')
            owner=p.get('ownerId')
            if owner is not None and not isinstance(owner,str):raise ConnectorError('Invalid Copilot owner identity; previous inventory retained.')
            evidence={k:p.get(k) for k in ['environmentId','createdBy','ownerId','lastPublishedAt','createdIn','entraAppId','entraAgentId','entraAgentBlueprintId','authentication','channels']}
            evidence['owner_type']='Microsoft Entra object ID; source-reported current owner'
            rows.append(agent('Microsoft Copilot','Copilot agent',ident,p.get('displayName') or a.get('name'),owner=owner,description=p.get('description'),evidence=evidence))
        first,second=continuation(data,'$skipToken'),continuation(data,'skipToken')
        if first and second and first!=second:raise ConnectorError('Ambiguous Power Platform continuation token.')
        skip=first or second or ''
        truncated=data.get('resultTruncated',False);total=data.get('totalRecords',len(rows))
        if truncated not in (True,False,0,1,'true','false') or type(total) is not int or total<0:raise ConnectorError('Invalid Power Platform pagination metadata.')
        if not skip:
            if truncated in (True,'true') or total>len(rows):raise ConnectorError('Truncated Power Platform inventory without continuation token.')
            return rows,[]
        if skip in seen:raise ConnectorError('Repeated Power Platform skip token.')
        seen.add(skip)
    raise ConnectorError('Power Platform pagination limit reached.')

def m365(t,c):
    rows=[];assets=[]
    for a in odata(t,'/beta/copilot/admin/catalog/packages'):
        elements=a.get('elementTypes');hosts=a.get('supportedHosts')
        if elements is None:elements=[]
        if hosts is None:hosts=[]
        if not isinstance(elements,list) or not isinstance(hosts,list) or any(not isinstance(v,str) for v in elements+hosts):raise ConnectorError('Invalid Microsoft catalog package classification.')
        evidence={**{k:a.get(k) for k in ('platform','isBlocked','availableTo','deployedTo','elementTypes','supportedHosts','governanceMetadata','agentIdentityId','lastModifiedDateTime')},'scope':'Organization catalog package; availability is not runtime health'}
        is_agent=any(v.lower() in ('bot','declarativeagent','customengineagent') for v in elements) or any(v.lower()=='copilot' for v in hosts)
        if is_agent:rows.append(agent('Microsoft 365','Agent package',a.get('id'),a.get('displayName') or a.get('name'),description=a.get('shortDescription') or a.get('description'),evidence=evidence))
        else:assets.append(asset('Microsoft 365','application',a.get('id'),a.get('displayName') or a.get('name'),a.get('shortDescription') or a.get('description'),{**evidence,'classification':'No explicit agent element or Copilot host returned'}))
    return rows,assets

def foundry(t,c):
    path='/api/projects/'+quote(c['project'],safe='')+'/agents';params={'api-version':'v1','limit':100};rows=[];seen=set()
    for _ in range(100):
        data=t.request(path,params).data
        for a in collection(data,'data'):
            rows.append(agent('Microsoft Foundry','Agent definition',a.get('id') or a.get('name'),a.get('name'),description=a.get('description'),evidence={'project':c['project'],'kind':a.get('kind'),'object':a.get('object'),'version':a.get('version')}))
        if type(data.get('has_more')) is not bool:raise ConnectorError('Foundry omitted a valid pagination completion flag.')
        if not data['has_more']:return rows,[]
        after=continuation(data,'last_id')
        if not after or after in seen:raise ConnectorError('Invalid Foundry pagination.')
        seen.add(after);params['after']=after
    raise ConnectorError('Foundry pagination limit reached.')

def google(t,c,runtime=False):
    parent='/v1/projects/'+quote(c['project'],safe='')+'/locations/'+quote(c['location'],safe='')
    path=parent+('/reasoningEngines' if runtime else '/agents');key='reasoningEngines' if runtime else 'agents';rows=[]
    for a in pages(t,path,key,{'pageSize':100},allow_missing=True):
        evidence={'project':c['project'],'location':c['location'],'createTime':a.get('createTime'),'updateTime':a.get('updateTime')}
        if not runtime:evidence.update({k:a.get(k) for k in ('agentId','version','protocols','skills','card')})
        rows.append(agent('Google Agent Runtime' if runtime else 'Google Agent Registry','Runtime' if runtime else 'Registered agent',a.get('name'),a.get('displayName') or a.get('display_name'),description=a.get('description'),evidence=evidence))
    return rows,[]

def langgraph(t,c):
    rows=[]
    for offset in range(0,10000,100):
        data=t.request('/assistants/search',body={'limit':100,'offset':offset},method='POST').data
        if not isinstance(data,list):raise ConnectorError('Expected a LangGraph assistants array.')
        for a in data:rows.append(agent('LangGraph','Assistant configuration',a.get('assistant_id'),a.get('name'),description=a.get('description'),evidence={'graph_id':a.get('graph_id'),'version':a.get('version'),'created_at':a.get('created_at'),'updated_at':a.get('updated_at')}))
        if len(data)<100:return rows,[]
    raise ConnectorError('LangGraph pagination limit reached.')

def salesforce(t,c,session_token):
    version=c['api_version']
    if not re.fullmatch(r'\d{2}\.0',version) or int(version.split('.')[0])<68:raise ConnectorError('Agentforce metadata connector requires API version 68.0 or newer.')
    body='<env:Envelope xmlns:env="http://schemas.xmlsoap.org/soap/envelope/" xmlns:m="http://soap.sforce.com/2006/04/metadata"><env:Header><m:SessionHeader><m:sessionId>'+escape(session_token)+'</m:sessionId></m:SessionHeader></env:Header><env:Body><m:listMetadata><m:queries><m:type>AiAgentDefinition</m:type></m:queries><m:asOfVersion>'+version+'</m:asOfVersion></m:listMetadata></env:Body></env:Envelope>'
    response=t.request('/services/Soap/m/'+version,body=body,method='POST',headers={'SOAPAction':'listMetadata'},xml=True)
    if '<!DOCTYPE' in response.text.upper() or '<!ENTITY' in response.text.upper():raise ConnectorError('Unsafe XML response rejected.')
    try:root=ET.fromstring(response.text)
    except ET.ParseError:raise ConnectorError('Invalid Salesforce metadata XML.') from None
    if root.tag!='{http://schemas.xmlsoap.org/soap/envelope/}Envelope':raise ConnectorError('Expected a Salesforce SOAP envelope.')
    if root.find('.//{http://schemas.xmlsoap.org/soap/envelope/}Fault') is not None:raise ConnectorError('Salesforce metadata read failed; check API version and permissions.')
    response=root.find('{http://schemas.xmlsoap.org/soap/envelope/}Body/{http://soap.sforce.com/2006/04/metadata}listMetadataResponse')
    if response is None:raise ConnectorError('Expected Salesforce listMetadataResponse; previous inventory retained.')
    results=response.findall('{http://soap.sforce.com/2006/04/metadata}result')
    if len(results)>=1000:raise ConnectorError('Salesforce listMetadata may be truncated at 1,000 records; narrow the source before importing.')
    rows=[]
    for r in results:
        def field(k):return r.findtext('{http://soap.sforce.com/2006/04/metadata}'+k)
        if not field('fullName'):raise ConnectorError('Invalid Agentforce metadata identity.')
        rows.append(agent('Salesforce','Agentforce definition',field('id') or field('fullName'),field('fullName'),evidence={'api_version':version,'metadata_type':'AiAgentDefinition','lastModifiedDate':field('lastModifiedDate'),'lastModifiedByName':field('lastModifiedByName')}))
    return rows,[]

def a2a(t,c):
    path=c.get('card_path') or '/.well-known/agent-card.json'
    if not path.startswith('/') or path.startswith('//') or '?' in path or '#' in path:raise ConnectorError('Agent Card path must be a local absolute URL path.')
    card=t.request(path).data
    if not isinstance(card,dict) or 'error' in card or card.get('errors') or not card.get('name') or not isinstance(card.get('skills'),list):raise ConnectorError('Invalid Agent Card: name and skills are required.')
    interfaces=card.get('supportedInterfaces') or card.get('additionalInterfaces') or []
    if not isinstance(interfaces,list) or any(not isinstance(i,dict) for i in interfaces) or any(not isinstance(skill,dict) for skill in card['skills']):raise ConnectorError('Invalid Agent Card interfaces or skills.')
    endpoint=card.get('url') or (interfaces[0].get('url') if interfaces else None)
    if not isinstance(endpoint,str) or urlsplit(endpoint).scheme not in ('http','https') or not urlsplit(endpoint).hostname or urlsplit(endpoint).username or urlsplit(endpoint).password:raise ConnectorError('Agent Card has no valid advertised HTTP endpoint.')
    row=agent('A2A','Advertised agent',t.origin+path,card['name'],description=card.get('description'),evidence={'card_url':t.origin+path,'endpoint':endpoint,'version':card.get('version'),'protocolVersion':card.get('protocolVersion'),'interfaces':interfaces,'skills':card['skills'],'capabilities':card.get('capabilities'),'provider':card.get('provider'),'signature_status':'Not verified; self-advertised metadata only'})
    if card.get('x-catproof-demo') is True:row['provenance']='seeded'
    return [row],[]

def entra(t,c):
    ids=[x.strip() for x in c['principal_ids'].split(',') if x.strip()]
    if len(ids)>100:raise ConnectorError('Limit Entra enrichment to 100 explicit principal IDs per connection.')
    assets=[]
    for pid in ids:
        if not re.fullmatch('[a-zA-Z0-9-]{1,100}',pid):raise ConnectorError('Invalid service principal object ID.')
        p=t.request('/v1.0/servicePrincipals/'+quote(pid,safe=''),{'$select':'id,appId,displayName,servicePrincipalType,accountEnabled'}).data
        if not isinstance(p,dict) or 'error' in p or p.get('errors') or not isinstance(p.get('id'),str) or p['id'].lower()!=pid.lower():raise ConnectorError('Entra returned a different principal from the configured identity.')
        owners=list(odata(t,'/v1.0/servicePrincipals/'+quote(pid,safe='')+'/owners?$select=id,displayName'))
        assets.append(asset('Microsoft Entra','identity',p['id'],p.get('displayName'),evidence={'appId':p.get('appId'),'servicePrincipalType':p.get('servicePrincipalType'),'accountEnabled':p.get('accountEnabled'),'directory_owners':[{'id':o.get('id'),'displayName':o.get('displayName')} for o in owners]}))
    return [],assets

def mcp(t,c):
    assets=[];params={'limit':100}
    if c.get('search'):params['search']=c['search']
    seen=set()
    for _ in range(100):
        data=t.request('/v0.1/servers',params).data
        for item in collection(data,'servers'):
            server=item.get('server')
            if not isinstance(server,dict) or not server.get('name'):raise ConnectorError('Invalid MCP registry server envelope.')
            source_identifier(server.get('version'))
            assets.append(asset('MCP Registry','mcp_server',server['name']+'@'+text(server.get('version')),server.get('title') or server['name'],server.get('description'),evidence={'server_name':server['name'],'version':server.get('version'),'repository':server.get('repository'),'remotes':server.get('remotes'),'status':(item.get('_meta') or {}).get('io.modelcontextprotocol.registry/official',{}).get('status')}))
            if (item.get('_meta') or {}).get('ai.catproof/demo') is True:assets[-1]['provenance']='seeded'
        metadata=data.get('metadata',{})
        if not isinstance(metadata,dict):raise ConnectorError('Invalid MCP pagination metadata.')
        cursor=continuation(metadata,'nextCursor')
        if not cursor:return [],assets
        if cursor in seen:raise ConnectorError('Repeated MCP registry cursor.')
        seen.add(cursor);params['cursor']=cursor
    raise ConnectorError('MCP registry pagination limit reached.')

def lovable(t,c):
    assets=[];seen_ids=set();seen_cursors=set();params={'workspace_id':c['workspace_id'],'limit':100}
    for _ in range(100):
        data=t.request('/v1/projects',params).data
        for a in collection(data,'data'):
            ident=a.get('id')
            if not isinstance(ident,str) or not ident or ident in seen_ids:raise ConnectorError('Missing or repeated Lovable project identity.')
            if a.get('workspace_id')!=c['workspace_id']:raise ConnectorError('Lovable returned a project outside the configured workspace.')
            if not isinstance(a.get('is_published'),bool):raise ConnectorError('Invalid Lovable publication status.')
            seen_ids.add(ident)
            evidence={k:a.get(k) for k in ('workspace_id','owner_id','is_published','publish_audience','visibility','created_at','last_edited_at')}
            evidence['project_url']='https://lovable.dev/projects/'+quote(ident,safe='')
            evidence['owner_type']='Lovable user ID; not a verified business owner'
            evidence['status_meaning']='Publication state only; runtime health and agent capabilities not assessed'
            row=asset('Lovable','application',ident,a.get('name'),a.get('description'),evidence)
            row.update(owner=text(a.get('owner_id')) or 'Unassigned',status='published' if a['is_published'] else 'unpublished')
            assets.append(row)
        pagination=data.get('pagination')
        if not isinstance(pagination,dict) or not isinstance(pagination.get('has_more'),bool):raise ConnectorError('Invalid Lovable pagination; previous inventory retained.')
        cursor=pagination.get('next_cursor')
        if not pagination['has_more']:
            if cursor is not None:raise ConnectorError('Inconsistent Lovable pagination.')
            return [],assets
        if not isinstance(cursor,str) or not cursor or cursor in seen_cursors:raise ConnectorError('Missing or repeated Lovable pagination cursor.')
        seen_cursors.add(cursor);params={**params,'cursor':cursor}
    raise ConnectorError('Lovable pagination limit reached; previous inventory retained.')

class AWSClientFactory:
    def __call__(self,service,region,prefix,env):
        try:import boto3
        except ImportError:raise ConnectorError('AWS connector unavailable. Contact CatProof support.') from None
        profile=env.get(prefix+'_PROFILE');key=env.get(prefix+'_ACCESS_KEY_ID');secret=env.get(prefix+'_SECRET_ACCESS_KEY')
        if profile and (key or secret):raise ConnectorError('Choose an AWS profile OR an access-key pair, not both.')
        if profile:session=boto3.Session(profile_name=profile,region_name=region)
        elif key and secret:session=boto3.Session(aws_access_key_id=key,aws_secret_access_key=secret,aws_session_token=env.get(prefix+'_SESSION_TOKEN'),region_name=region)
        else:raise ConnectorError('AWS credentials missing. Open this connection and configure an access key, secret key and session token when issued.')
        from botocore.config import Config
        return session.client(service,config=Config(connect_timeout=10,read_timeout=20,retries={'max_attempts':2}))

def aws_pages(client,method,key,params=None):
    params=dict(params or {});seen=set()
    for _ in range(100):
        data=getattr(client,method)(**params)
        yield from collection(data,key)
        next_token=continuation(data,'nextToken')
        if not next_token:return
        if next_token in seen:raise ConnectorError('Repeated AWS pagination token.')
        seen.add(next_token);params['nextToken']=next_token
    raise ConnectorError('AWS pagination limit reached.')

def aws(kind,c,prefix,env,factory):
    region=c['region']
    if not re.fullmatch(r'[a-z]{2}(?:-[a-z]+)+-\d',region):raise ConnectorError('Invalid AWS region.')
    service={'aws_bedrock':'bedrock-agent','aws_agentcore':'bedrock-agentcore-control','aws_registry':'agent-registry-control'}[kind]
    client=factory(service,region,prefix,env);rows=[];assets=[]
    if kind=='aws_bedrock':
        for a in aws_pages(client,'list_agents','agentSummaries',{'maxResults':100}):rows.append(agent('AWS Bedrock','Agent definition',a.get('agentId'),a.get('agentName'),status=a.get('agentStatus'),description=a.get('description'),evidence={'region':region,'updatedAt':text(a.get('updatedAt'))}))
    elif kind=='aws_agentcore':
        for a in aws_pages(client,'list_agent_runtimes','agentRuntimes',{'maxResults':100}):rows.append(agent('AWS AgentCore','Agent runtime',a.get('agentRuntimeArn') or a.get('agentRuntimeId'),a.get('agentRuntimeName'),status=a.get('status'),description=a.get('description'),evidence={'region':region,'agentRuntimeVersion':a.get('agentRuntimeVersion')}))
    else:
        for a in aws_pages(client,'list_registry_records','registryRecords',{'registryId':c['registry_id'],'maxResults':100}):
            record_type=source_identifier(a.get('recordType')).upper();identifier=a.get('recordArn') or a.get('recordId')
            evidence={'registry_id':c['registry_id'],'region':region,'record_type':record_type,'approval_status':a.get('status'),'record_version':a.get('recordVersion'),'createdBy':a.get('createdBy')}
            if record_type=='AGENT':rows.append(agent('AWS Agent Registry','Registered agent',identifier,a.get('displayName') or a.get('name'),description=a.get('description'),evidence=evidence))
            else:assets.append(asset('AWS Agent Registry','mcp_server' if record_type=='MCP' else 'registry_resource',identifier,a.get('displayName') or a.get('name'),a.get('description'),evidence))
    return rows,assets

def read_connector(connection,env,transport_factory=Transport,aws_factory=None):
    kind=connection['kind'];prefix=connection.get('credential_prefix','');config=connection.get('config') or {}
    if isinstance(config,str):config=json.loads(config)
    config=validate_config(kind,config)
    if kind not in CATALOG:raise ConnectorError('Unknown connector adapter.')
    try:
        if kind.startswith('aws_'):rows,assets=aws(kind,config,prefix,env,aws_factory or AWSClientFactory())
        else:
            if kind=='lovable' and connection['url'].rstrip('/')!='https://api.lovable.dev':raise ConnectorError('Lovable requires the official origin https://api.lovable.dev.')
            if kind=='lovable' and not env.get(prefix+'_TOKEN'):raise ConnectorError('Lovable API key is missing. Open this connection and choose Configure API key.')
            auth=token(env,prefix,required=kind not in ('a2a','mcp') or bool(prefix));headers={}
            if auth:headers['x-api-key' if kind=='langgraph' else 'Authorization']=auth if kind=='langgraph' else 'Bearer '+auth
            if kind=='lovable':headers={'Lovable-API-Key':auth,'Lovable-Version':'2026-09-11'}
            if kind=='snowflake' and config.get('role'):headers['X-Snowflake-Role']=config['role']
            if kind in ('google_registry','google_runtime') and config.get('billing_project'):headers['X-Goog-User-Project']=config['billing_project']
            t=transport_factory(connection['url'],headers)
            if kind=='salesforce':rows,assets=salesforce(t,config,auth)
            elif kind=='google_registry':rows,assets=google(t,config)
            elif kind=='google_runtime':rows,assets=google(t,config,True)
            else:rows,assets=globals()[kind](t,config)
        if len(rows)+len(assets)>10000:raise ConnectorError('Connector inventory exceeds the 10,000 record limit.')
        return {'agents':rows,'assets':assets,'sources':[{'name':CATALOG[kind]['label'],'health':'ok','mode':'live'}]}
    except ConnectorError:raise
    except Exception:raise ConnectorError('Connector read failed or returned an unsupported response. Verify scope, permissions and API version.') from None
