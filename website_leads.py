"""Public website enquiries and explicitly opted-in updates; no email sending."""
import re
import hashlib
import time

POLICY_VERSION='2026-10-28'

def validate_lead(data):
    if not isinstance(data,dict):raise ValueError('Invalid submission.')
    allowed={'kind','email','consent','website','name','company','platforms','message'}
    if set(data)-allowed:raise ValueError('Invalid submission fields.')
    if any(not isinstance(v,str) for k,v in data.items() if k!='consent'):
        raise ValueError('Invalid submission fields.')
    if data.get('website',''):return None  # Honeypot: never persist bots.
    kind=data.get('kind')
    if kind not in ('updates','contact'):raise ValueError('Invalid submission type.')
    email=data.get('email','').strip().lower()
    if len(email)>254 or not re.fullmatch(r'[a-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+',email):
        raise ValueError('Enter a valid email address.')
    if data.get('consent') is not True:raise ValueError('Please confirm the consent checkbox.')
    fields={}
    for key,limit in (('name',120),('company',200),('platforms',500),('message',5000)):
        value=data.get(key,'').strip()
        if len(value)>limit or '\x00' in value:raise ValueError('Please shorten your '+key+'.')
        if kind=='contact' and key!='platforms' and not value:raise ValueError('Enter your '+key+'.')
        fields[key]=value if kind=='contact' else ''
    return dict(fields,email=email,kind=kind)

class WebsiteLeads:
    def __init__(self,accounts):self.accounts=accounts
    def rate_allowed(self,email):
        # Separate quota keys keep public signups out of account-login budgets.
        now=time.time();allowed=True
        with self.accounts.connect(self.accounts.url,connect_timeout=5) as db:
            db.execute('DELETE FROM catproof_auth_limits WHERE window_start<%s',(now-600,))
            for identity,limit in (('website-email:'+email,10),('website-global',100)):
                key=hashlib.sha256(identity.encode()).hexdigest()
                row=db.execute('INSERT INTO catproof_auth_limits(key,window_start,attempts) VALUES(%s,%s,1) ON CONFLICT(key) DO UPDATE SET attempts=catproof_auth_limits.attempts+1 RETURNING attempts',(key,now)).fetchone()
                allowed=allowed and row[0]<=limit
        return allowed
    def capture(self,data):
        lead=validate_lead(data)
        if lead is None:return
        with self.accounts.connect(self.accounts.url,connect_timeout=5) as db:
            db.execute('''INSERT INTO catproof_website_leads
                (email,kind,name,company,platforms,message,policy_version)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(email,kind) DO UPDATE SET
                name=EXCLUDED.name,company=EXCLUDED.company,platforms=EXCLUDED.platforms,
                message=EXCLUDED.message,policy_version=EXCLUDED.policy_version,
                consented_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP''',
                tuple(lead[k] for k in ('email','kind','name','company','platforms','message'))+(POLICY_VERSION,))
