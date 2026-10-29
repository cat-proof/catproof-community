"""Public auth form contracts and strict secret-field allowlists. No credential values."""
OPTIONAL={'a2a','mcp','atlas','discovery'}
SUFFIXES=('TOKEN','USERNAME','PASSWORD','ACCESS_KEY_ID','SECRET_ACCESS_KEY','SESSION_TOKEN','PROFILE')
def field(key,label,required=True):return dict(key=key,label=label,required=required)
def auth_spec(kind):
    if kind in ('local','seeded'):return dict(modes=[],hint='Enroll local devices with a one-time code from Local devices.')
    if kind.startswith('aws_'):
        modes=[dict(id='aws',label='AWS access keys',fields=[field('access_key_id','Access key ID'),field('secret_access_key','Secret access key'),field('session_token','Session token (for temporary credentials)',False)])]
        hint='Prefer temporary, read-only AWS credentials. Include the session token when issued. Replace expired credentials here; automatic renewal is not yet supported.'
    else:
        labels={'lovable':'Lovable workspace API key','databricks':'Databricks access token','snowflake':'Snowflake PAT or OAuth access token','copilot':'Delegated Power Platform access token','m365':'Microsoft Graph access token','entra':'Microsoft Graph access token','foundry':'Microsoft Entra access token for ai.azure.com','google_registry':'Google OAuth access token','google_runtime':'Google OAuth access token','langgraph':'LangSmith API key','salesforce':'Salesforce OAuth access token'}
        modes=[dict(id='token',label='API key / bearer token',fields=[field('token',labels.get(kind,'Bearer token'))])]
        if kind=='servicenow':modes.append(dict(id='basic',label='Username and password',fields=[field('username','ServiceNow username'),field('password','ServiceNow password')]))
        if kind in OPTIONAL:modes.insert(0,dict(id='none',label='Public endpoint · no authentication',fields=[]))
        hint='Use a provider-issued credential with read-only access. Short-lived tokens must be replaced here when they expire; OAuth sign-in and automatic refresh are not yet supported.'
        if kind=='lovable':hint='Use a Business/Enterprise workspace API key with Workspace: Read access.'
        if kind=='m365':hint='Use a Microsoft Graph token with agent catalog read access and the required Agent 365 license. Replace expired tokens here.'
        if kind=='entra':hint='Use a Microsoft Graph token with approved directory read access, such as Application.Read.All. Only configured service principal IDs and owners are read.'
        if kind=='copilot':hint='Use a delegated Power Platform token. Microsoft Graph and app-only tokens are not supported. Replace the token here when it expires.'
    return dict(modes=modes,hint=hint)

def validate_credentials(kind,credentials):
    if not isinstance(credentials,dict) or set(credentials)-{'mode','token','username','password','access_key_id','secret_access_key','session_token'}:raise ValueError('Invalid credential fields')
    mode=credentials.get('mode');spec=auth_spec(kind)
    match=next((m for m in spec['modes'] if m['id']==mode),None)
    if match is None:raise ValueError('Choose a supported authentication method')
    allowed={f['key'] for f in match['fields']}
    if set(credentials)-({'mode'}|allowed):raise ValueError('Credential fields do not match the authentication method')
    result={}
    for f in match['fields']:
        value=credentials.get(f['key'],'')
        if not isinstance(value,str) or len(value)>8192 or any(ord(c)<32 or ord(c)>126 for c in value):raise ValueError('Invalid credential value')
        # Passwords are opaque: spaces are significant and must never be trimmed.
        if f['key']!='password':
            value=value.strip()
            if any(c.isspace() for c in value):raise ValueError('Invalid credential value')
        if f['required'] and not value:raise ValueError('Complete all required credential fields')
        if value:result[f['key'].upper()]=value
    return result
