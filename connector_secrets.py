"""Registry/connection-scoped authenticated encryption; plaintext never enters snapshots."""
import json
import time
import uuid
from cryptography.fernet import Fernet,MultiFernet,InvalidToken
from federation import FederatedRegistry
from adapters.auth import validate_credentials,auth_spec,OPTIONAL

class ConnectorSecrets:
    def __init__(self,accounts,keys=''):
        self.accounts=accounts
        self.cipher=MultiFernet([Fernet(k.strip().encode()) for k in keys.split(',') if k.strip()]) if keys else None
    def encrypt(self,registry,cid,key):
        if not self.cipher:raise ValueError('Secure credential storage is temporarily unavailable. Contact support.')
        if isinstance(key,str):
            if not 8<=len(key.strip())<=4096 or any(ord(c)<33 or ord(c)>126 for c in key.strip()):raise ValueError('Enter a valid API key.')
            key=key.strip()
        elif not isinstance(key,dict):raise ValueError('Invalid credential request')
        return self.cipher.encrypt(json.dumps({'registry':registry,'connection':cid,'token':key}).encode()).decode()
    def load(self,db,registry):
        result={}
        for cid,encrypted in db.execute('SELECT connection_id,ciphertext FROM catproof_connector_secrets WHERE registry_id=%s',(registry,)).fetchall():
            if encrypted=='':
                result[cid]={} # non-secret removal marker blocks legacy credential fallback
                continue
            if not self.cipher:raise RuntimeError('Credential encryption key unavailable')
            try:
                data=json.loads(self.cipher.decrypt(encrypted.encode()))
                if data['registry']!=registry or data['connection']!=cid:raise ValueError()
                result[cid]=data['token']
            except (InvalidToken,ValueError,KeyError,TypeError):raise RuntimeError('Saved credential cannot be decrypted') from None
        return result
    def change(self,actor,data,connect=False,remove=False):
        allowed={'api_key','name','workspace_id','kind','url','source_filter','config','credentials'} if connect else {'api_key','credentials','connection_id'}
        if not isinstance(data,dict) or set(data)-allowed or any(not isinstance(v,dict if k in ('config','credentials') else str) for k,v in data.items()):raise ValueError('Invalid credential request')
        if 'api_key' in data and 'credentials' in data:raise ValueError('Choose one credential format')
        registry=actor['workspace_id'];accounts=self.accounts
        with accounts.connect(accounts.url,connect_timeout=5) as db:
            accounts.locked(db,registry);accounts.owner(db,actor)
            store=FederatedRegistry(env={},workspace_id=registry,db=db);store.actor=actor['email']
            try:
                if connect:
                    kind=data.get('kind','lovable')
                    if not auth_spec(kind)['modes']:raise ValueError('This connector uses device enrollment instead')
                    body={'name':data.get('name',''),'kind':kind,'url':data.get('url','https://api.lovable.dev' if kind=='lovable' else ''),'credential_prefix':kind.upper(),'source_filter':data.get('source_filter',''),'config':data.get('config',{'workspace_id':data.get('workspace_id','').strip()} if kind=='lovable' else {})}
                    cid=store.add_connection(body)
                else:cid=data.get('connection_id','')
                connection=store.db.execute('SELECT kind FROM catproof_connections WHERE id=?',(cid,)).fetchone()
                if not connection or not auth_spec(connection[0])['modes']:raise ValueError('Connection not found')
                kind=connection[0]
                if not remove:store.require_connection_available(cid)
                if remove:
                    store.set_enabled(cid,False)
                    db.execute("INSERT INTO catproof_connector_secrets(registry_id,connection_id,ciphertext,updated_at) VALUES(%s,%s,'',%s) ON CONFLICT(registry_id,connection_id) DO UPDATE SET ciphertext='',updated_at=excluded.updated_at",(registry,cid,time.time()))
                else:
                    if 'api_key' in data:
                        if kind!='lovable':raise ValueError('Use the connector credential form')
                        credential=data['api_key'] # compatibility with existing Lovable clients
                    else:credential=validate_credentials(kind,data.get('credentials'))
                    store.db.execute('UPDATE catproof_connections SET credential_prefix=? WHERE id=?',(kind.upper() if credential or kind not in OPTIONAL else '',cid))
                    encrypted=self.encrypt(registry,cid,credential)
                    db.execute('INSERT INTO catproof_connector_secrets(registry_id,connection_id,ciphertext,updated_at) VALUES(%s,%s,%s,%s) ON CONFLICT(registry_id,connection_id) DO UPDATE SET ciphertext=excluded.ciphertext,updated_at=excluded.updated_at',(registry,cid,encrypted,time.time()))
                if not remove:
                    store.db.execute('UPDATE catproof_connections SET error=NULL,last_success=NULL,last_attempt=NULL,sync_generation=? WHERE id=?',(uuid.uuid4().hex,cid))
                    store.db.execute('UPDATE catproof_observations SET stale=1 WHERE connection_id=?',(cid,))
                    store.db.execute('UPDATE catproof_assets SET stale=1 WHERE connection_id=?',(cid,))
                accounts.event(db,registry,actor['id'],'connector.credential_removed' if remove else 'connector.credential_saved',cid)
            finally:store.db.close()
        return {'connection_id':cid,'credential_saved':not remove}
