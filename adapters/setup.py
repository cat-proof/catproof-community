"""Offline configuration checks. Returns variable names and booleans, never secret values."""
import importlib.util
import json
from .catalog import validate_config

TOP_FIVE=('snowflake','copilot','aws_bedrock','a2a','google_registry')
GUIDE_FAMILIES={'lovable':'lovable','snowflake':'snowflake','copilot':'copilot','aws_bedrock':'aws','aws_agentcore':'aws','aws_registry':'aws','a2a':'a2a','google_registry':'google','google_runtime':'google'}

def check_setup(connection,env):
    kind=connection['kind'];prefix=connection.get('credential_prefix','');checks=[]
    config=connection.get('config') or {}
    if isinstance(config,str):config=json.loads(config)
    try:validate_config(kind,config);checks.append({'label':'Required scope fields','ok':True,'detail':'Configured'})
    except ValueError as e:checks.append({'label':'Required scope fields','ok':False,'detail':str(e)})
    def add(label,ok,detail):checks.append({'label':label,'ok':bool(ok),'detail':detail})
    if kind=='lovable':
        add('Official Lovable origin',connection.get('url','').rstrip('/')=='https://api.lovable.dev','Use https://api.lovable.dev')
        add('Lovable workspace API key',bool(env.get(prefix+'_TOKEN')),'Key configured' if env.get(prefix+'_TOKEN') else 'Add your Workspace: Read API key using Configure API key on this connection')
    elif kind.startswith('aws_'):
        add('AWS SDK',importlib.util.find_spec('boto3') is not None,'Contact CatProof support if the AWS connector is unavailable')
        profile=env.get(prefix+'_PROFILE');key=env.get(prefix+'_ACCESS_KEY_ID');secret=env.get(prefix+'_SECRET_ACCESS_KEY')
        add('AWS credentials',bool(profile) != bool(key and secret) and not (profile and (key or secret)),'Use Configure credentials to save an access key ID, secret access key and session token when issued')
    elif kind=='servicenow':
        add('ServiceNow credentials',env.get(prefix+'_TOKEN') or (env.get(prefix+'_USERNAME') and env.get(prefix+'_PASSWORD')),'Use Configure credentials to save a bearer token or username and password')
    elif kind not in ('seeded',) and (kind not in ('atlas','discovery','a2a','mcp') or prefix):
        add('Bearer/API token',bool(env.get(prefix+'_TOKEN')),'Configured' if env.get(prefix+'_TOKEN') else 'Use Configure credentials to save a read-only API key or access token')
    else:add('Authentication configuration',True,'Public endpoint selected; no authentication required')
    add('Connection enabled',bool(connection.get('enabled',True)),'Paused sources are never contacted')
    return {'connection_id':connection['id'],'ready':all(c['ok'] for c in checks),'checks':checks,
            'limitation':'Offline checks only. Sync verifies actual network access, token validity and source permissions.'}

from .catalog import CATALOG,BASE_CATALOG
for _kind in {**BASE_CATALOG,**CATALOG}:
    if _kind!='local':GUIDE_FAMILIES.setdefault(_kind,'credentials')
