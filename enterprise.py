"""Registry membership, revocable invitations, session controls and security audit.

Database transactions enforce permissions; email domains never grant membership.
Invitations are manually delivered bearer secrets, not email verification.
"""
import hashlib
import json
import secrets
import time
import uuid
from accounts import Accounts,password_matches
from registry_locks import lock_registry

ROLES=('owner','editor','viewer')
EDIT_ACTIONS={'/api/reconcile','/api/agents','/api/agents/import','/api/agents/assign-owner','/api/connection-preview','/api/annotate','/api/asset-link','/api/link','/api/unlink','/api/refresh','/api/connection-check'}
OWNER_ACTIONS={'/api/connections','/api/connection-state','/api/connection-archive'}
def can_write(role,path):return role=='owner' and path in EDIT_ACTIONS|OWNER_ACTIONS or role=='editor' and path in EDIT_ACTIONS

def digest(value):return hashlib.sha256(value.encode()).hexdigest()

class RegistryAccounts(Accounts):
    idle_seconds=1800
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
    def locked(self,db,registry_id=None):
        if registry_id is None:
            db.execute("SET LOCAL lock_timeout = '5s'")
            db.execute('SELECT pg_advisory_xact_lock(7102770)')
        else:lock_registry(db,registry_id)
    def event(self,db,registry,actor,action,target=''):
        db.execute('INSERT INTO catproof_security_events(id,registry_id,at,actor,action,target) VALUES(%s,%s,%s,%s,%s,%s)',('audit_'+uuid.uuid4().hex,registry,time.time(),actor,action,target))
    def owner(self,db,actor,password=None):
        row=db.execute('SELECT m.role,u.password_hash FROM catproof_members m JOIN catproof_users u ON u.id=m.user_id WHERE m.registry_id=%s AND m.user_id=%s',(actor['workspace_id'],actor['id'])).fetchone()
        if not row or row[0]!='owner':raise PermissionError('Only registry owners can manage access.')
        if password is not None and not password_matches(password,row[1]):raise PermissionError('Confirm your personal account password to manage access.')
    def issue(self,db,user_id):
        row=db.execute('SELECT workspace_id FROM catproof_users WHERE id=%s',(user_id,)).fetchone();wid=row[0]
        created=db.execute('INSERT INTO catproof_member_initializations(user_id) VALUES(%s) ON CONFLICT(user_id) DO NOTHING RETURNING user_id',(user_id,)).fetchone()
        if created:db.execute("INSERT INTO catproof_members(registry_id,user_id,role) VALUES(%s,%s,'owner') ON CONFLICT(registry_id,user_id) DO NOTHING",(wid,user_id))
        if not db.execute('SELECT 1 FROM catproof_members WHERE registry_id=%s AND user_id=%s',(wid,user_id)).fetchone():
            other=db.execute('SELECT registry_id FROM catproof_members WHERE user_id=%s ORDER BY registry_id LIMIT 1',(user_id,)).fetchone()
            if not other:raise ValueError('This account has no registry membership.')
            wid=other[0]
        token=super().issue(db,user_id);now=time.time()
        db.execute('INSERT INTO catproof_session_meta(token_hash,registry_id,created_at,last_seen) VALUES(%s,%s,%s,%s)',(digest(token),wid,now,now))
        self.event(db,wid,user_id,'session.created')
        return token
    def resolve(self,token,touch=True):
        if not token or len(token)>100:return None
        key=digest(token);now=time.time()
        with self.connect(self.url,connect_timeout=5) as db:
            row=db.execute('SELECT u.id,u.email,u.name,COALESCE(sm.registry_id,u.workspace_id),w.name,m.role,sm.last_seen FROM catproof_sessions s JOIN catproof_users u ON u.id=s.user_id LEFT JOIN catproof_session_meta sm ON sm.token_hash=s.token_hash JOIN catproof_workspaces w ON w.id=COALESCE(sm.registry_id,u.workspace_id) JOIN catproof_members m ON m.registry_id=w.id AND m.user_id=u.id WHERE s.token_hash=%s AND s.expires_at>%s',(key,now)).fetchone()
            if not row:return None
            if row[6] is not None and row[6]<now-self.idle_seconds:
                if touch:
                    db.execute('DELETE FROM catproof_sessions WHERE token_hash=%s',(key,));self.event(db,row[3],row[0],'session.idle_expired')
                return None
            if touch:db.execute('INSERT INTO catproof_session_meta(token_hash,registry_id,created_at,last_seen) VALUES(%s,%s,%s,%s) ON CONFLICT(token_hash) DO UPDATE SET last_seen=excluded.last_seen',(key,row[3],now,now))
            return dict(self.principal(row),role=row[5],registry_id=row[3],registry_name=row[4])
    def revoke(self,token):
        actor=self.resolve(token)
        with self.connect(self.url,connect_timeout=5) as db:
            if actor:self.event(db,actor['workspace_id'],actor['id'],'session.signed_out')
            db.execute('DELETE FROM catproof_sessions WHERE token_hash=%s',(digest(token),))
    def rate_allowed(self,identity):
        now=time.time();allowed=True
        with self.connect(self.url,connect_timeout=5) as db:
            db.execute('DELETE FROM catproof_auth_limits WHERE window_start<%s',(now-600,))
            for key,limit in ((digest('identity:'+identity.strip().lower()[:254]),10),(digest('global'),100)):
                row=db.execute('INSERT INTO catproof_auth_limits(key,window_start,attempts) VALUES(%s,%s,1) ON CONFLICT(key) DO UPDATE SET attempts=catproof_auth_limits.attempts+1 RETURNING attempts',(key,now)).fetchone()
                allowed=allowed and row[0]<=limit
        return allowed
    def security(self,actor,token):
        with self.connect(self.url,connect_timeout=5) as db:
            members=db.execute('SELECT u.id,u.name,u.email,m.role FROM catproof_members m JOIN catproof_users u ON u.id=m.user_id WHERE m.registry_id=%s ORDER BY u.name',(actor['workspace_id'],)).fetchall()
            registries=db.execute('SELECT w.id,w.name,m.role FROM catproof_members m JOIN catproof_workspaces w ON w.id=m.registry_id WHERE m.user_id=%s ORDER BY w.name',(actor['id'],)).fetchall()
            sessions=db.execute('SELECT s.token_hash,sm.created_at,sm.last_seen,s.expires_at FROM catproof_sessions s LEFT JOIN catproof_session_meta sm ON sm.token_hash=s.token_hash WHERE s.user_id=%s AND s.expires_at>%s',(actor['id'],time.time())).fetchall()
            result={'role':actor['role'],'registries':[dict(zip(('id','name','role'),r)) for r in registries],'members':[dict(zip(('id','name','email','role'),r)) for r in members],'sessions':[{'id':r[0],'current':r[0]==digest(token),'created_at':r[1],'last_seen':r[2],'expires_at':r[3]} for r in sessions],'idle_minutes':self.idle_seconds//60,'email_verified':False,'invitation_delivery':'Owner shares a private invitation code; email delivery is not configured.'}
            if actor['role']=='owner':
                self.owner(db,actor)
                invites=db.execute('SELECT id,email,role,expires_at FROM catproof_invites WHERE registry_id=%s AND used_at IS NULL AND expires_at>%s',(actor['workspace_id'],time.time())).fetchall()
                events=db.execute('SELECT id,at,actor,action,target FROM catproof_security_events WHERE registry_id=%s ORDER BY at DESC LIMIT 500',(actor['workspace_id'],)).fetchall()
                tokens=db.execute('SELECT id,label,created_at,expires_at,revoked_at FROM catproof_peer_tokens WHERE registry_id=%s ORDER BY created_at DESC',(actor['workspace_id'],)).fetchall()
                result.update(invites=[dict(zip(('id','email','role','expires_at'),r)) for r in invites],events=[dict(zip(('id','at','actor','action','target'),r)) for r in events],peer_tokens=[dict(zip(('id','label','created_at','expires_at','revoked_at'),r)) for r in tokens])
            return result
    def invite(self,actor,email,role,password):
        email=email.strip().lower()
        from accounts import validate_signup
        validate_signup({'name':'Invitation','email':email,'workspace':'Registry','password':'not-a-real-password'})
        if role not in ('editor','viewer'):raise ValueError('Invite an editor or viewer; promote an accepted member separately.')
        token=secrets.token_urlsafe(32);iid='invite_'+uuid.uuid4().hex;expires=time.time()+172800
        with self.connect(self.url,connect_timeout=5) as db:
            self.locked(db,actor['workspace_id']);self.owner(db,actor,password)
            db.execute('UPDATE catproof_invites SET used_at=%s WHERE registry_id=%s AND email=%s AND used_at IS NULL',(time.time(),actor['workspace_id'],email))
            db.execute('INSERT INTO catproof_invites(id,token_hash,registry_id,email,role,expires_at,created_by) VALUES(%s,%s,%s,%s,%s,%s,%s)',(iid,digest(token),actor['workspace_id'],email,role,expires,actor['id']))
            self.event(db,actor['workspace_id'],actor['id'],'member.invited',email)
        return {'id':iid,'code':token,'email':email,'role':role,'expires_at':expires}
    def accept(self,actor,token,session):
        with self.connect(self.url,connect_timeout=5) as db:
            target=db.execute('SELECT registry_id FROM catproof_invites WHERE token_hash=%s',(digest(token),)).fetchone()
            if not target:raise ValueError('Invitation is invalid.')
            self.locked(db,target[0])
            row=db.execute('SELECT id,registry_id,email,role FROM catproof_invites WHERE token_hash=%s AND used_at IS NULL AND expires_at>%s',(digest(token),time.time())).fetchone()
            if not row or row[2]!=actor['email']:raise ValueError('Invitation is invalid, expired or for a different email.')
            db.execute('INSERT INTO catproof_members(registry_id,user_id,role) VALUES(%s,%s,%s) ON CONFLICT(registry_id,user_id) DO NOTHING',(row[1],actor['id'],row[3]))
            db.execute('UPDATE catproof_invites SET used_at=%s WHERE id=%s',(time.time(),row[0]))
            db.execute('UPDATE catproof_session_meta SET registry_id=%s WHERE token_hash=%s AND token_hash IN (SELECT token_hash FROM catproof_sessions WHERE user_id=%s)',(row[1],digest(session),actor['id']))
            self.event(db,row[1],actor['id'],'invitation.accepted',row[0])
    def switch(self,actor,registry_id,token):
        with self.connect(self.url,connect_timeout=5) as db:
            self.locked(db,registry_id)
            if not db.execute('SELECT 1 FROM catproof_members WHERE registry_id=%s AND user_id=%s',(registry_id,actor['id'])).fetchone():raise PermissionError('You are not a member of this registry.')
            db.execute('UPDATE catproof_session_meta SET registry_id=%s WHERE token_hash=%s AND token_hash IN (SELECT token_hash FROM catproof_sessions WHERE user_id=%s)',(registry_id,digest(token),actor['id']))
            self.event(db,registry_id,actor['id'],'registry.opened')
    def member_change(self,actor,user_id,role,password):
        if role not in (*ROLES,'remove'):raise ValueError('Choose owner, editor, viewer or remove.')
        with self.connect(self.url,connect_timeout=5) as db:
            self.locked(db,actor['workspace_id']);self.owner(db,actor,password)
            current=db.execute('SELECT role FROM catproof_members WHERE registry_id=%s AND user_id=%s',(actor['workspace_id'],user_id)).fetchone()
            if not current:raise ValueError('Member not found.')
            if current[0]=='owner' and role!='owner':
                count=db.execute("SELECT count(*) FROM catproof_members WHERE registry_id=%s AND role='owner'",(actor['workspace_id'],)).fetchone()[0]
                if count<=1:raise ValueError('The registry must retain at least one owner.')
            if role=='remove':
                db.execute('DELETE FROM catproof_members WHERE registry_id=%s AND user_id=%s',(actor['workspace_id'],user_id))
                db.execute('DELETE FROM catproof_sessions WHERE user_id=%s AND token_hash IN (SELECT token_hash FROM catproof_session_meta WHERE registry_id=%s)',(user_id,actor['workspace_id']))
            else:db.execute('UPDATE catproof_members SET role=%s WHERE registry_id=%s AND user_id=%s',(role,actor['workspace_id'],user_id))
            if role!='owner':
                db.execute('UPDATE catproof_invites SET used_at=%s WHERE registry_id=%s AND created_by=%s AND used_at IS NULL',(time.time(),actor['workspace_id'],user_id))
                db.execute('UPDATE catproof_peer_tokens SET revoked_at=%s WHERE registry_id=%s AND creator=%s AND revoked_at IS NULL',(time.time(),actor['workspace_id'],user_id))
            self.event(db,actor['workspace_id'],actor['id'],'member.'+('removed' if role=='remove' else 'role_changed'),user_id+':'+role)
    def cancel_invite(self,actor,iid,password):
        with self.connect(self.url,connect_timeout=5) as db:
            self.locked(db,actor['workspace_id']);self.owner(db,actor,password)
            db.execute('UPDATE catproof_invites SET used_at=%s WHERE id=%s AND registry_id=%s AND used_at IS NULL',(time.time(),iid,actor['workspace_id']))
            self.event(db,actor['workspace_id'],actor['id'],'invitation.revoked',iid)
    def revoke_sessions(self,actor,session_id,token):
        with self.connect(self.url,connect_timeout=5) as db:
            if session_id=='others':db.execute('DELETE FROM catproof_sessions WHERE user_id=%s AND token_hash<>%s',(actor['id'],digest(token)))
            else:db.execute('DELETE FROM catproof_sessions WHERE user_id=%s AND token_hash=%s',(actor['id'],session_id))
            self.event(db,actor['workspace_id'],actor['id'],'session.revoked','other_sessions' if session_id=='others' else 'selected_session')
    def bootstrap_allowed(self):
        with self.connect(self.url,connect_timeout=5) as db:
            return not db.execute("SELECT 1 FROM catproof_security_events WHERE action='bootstrap.retired' LIMIT 1").fetchone()
    def legacy_login(self,user):
        uid='legacy_'+user
        with self.connect(self.url,connect_timeout=5) as db:
            self.locked(db,'legacy')
            if db.execute("SELECT 1 FROM catproof_security_events WHERE action='bootstrap.retired' LIMIT 1").fetchone():return None
            row=db.execute('SELECT password_hash FROM catproof_users WHERE id=%s',(uid,)).fetchone()
            if row and row[0]!='bootstrap-only':return None
            db.execute('INSERT INTO catproof_users(id,email,name,workspace_id,password_hash,created_at) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING',(uid,user,user,'legacy','bootstrap-only',time.time()))
            return {'id':uid,'email':user,'name':user,'workspace_id':'legacy','workspace_name':'CatProof team','email_verified':False},self.issue(db,uid)
    def upgrade(self,actor,data):
        from accounts import validate_signup,password_hash
        name,email,_,password=validate_signup({'name':data.get('name',actor['name']),'email':data.get('email',''),'workspace':'Registry','password':data.get('new_password','')})
        with self.connect(self.url,connect_timeout=5) as db:
            self.locked(db,actor['workspace_id']);self.owner(db,actor)
            row=db.execute('SELECT password_hash FROM catproof_users WHERE id=%s',(actor['id'],)).fetchone()
            if actor['workspace_id']!='legacy' or not actor['id'].startswith('legacy_') or not row or row[0]!='bootstrap-only':raise PermissionError('Only an existing bootstrap account can be upgraded.')
            db.execute('UPDATE catproof_users SET name=%s,email=%s,password_hash=%s WHERE id=%s',(name,email,password_hash(password),actor['id']))
            db.execute('DELETE FROM catproof_sessions WHERE user_id IN (SELECT id FROM catproof_users WHERE workspace_id=%s)',('legacy',))
            self.event(db,'legacy',actor['id'],'bootstrap.retired','Shared team sign-in disabled; use personal accounts and invitations.')
            return self.issue(db,actor['id'])

    def create_peer_token(self,actor,label,password):
        if not isinstance(label,str) or not 1<=len(label.strip())<=100 or any(ord(c)<32 for c in label):
            raise ValueError('Enter a peer label (1–100 characters).')
        token='cp_peer_'+secrets.token_urlsafe(32);identifier='peer_'+uuid.uuid4().hex;now=time.time()
        with self.connect(self.url,connect_timeout=5) as db:
            self.locked(db,actor['workspace_id']);self.owner(db,actor,password)
            count=db.execute('SELECT count(*) FROM catproof_peer_tokens WHERE registry_id=%s AND revoked_at IS NULL AND expires_at>%s',(actor['workspace_id'],now)).fetchone()[0]
            if count>=20:raise ValueError('Revoke an existing peer token before creating another (limit 20).')
            db.execute('INSERT INTO catproof_peer_tokens(id,registry_id,creator,label,token_hash,created_at,expires_at) VALUES(%s,%s,%s,%s,%s,%s,%s)',(identifier,actor['workspace_id'],actor['id'],label.strip(),digest(token),now,now+7776000))
            self.event(db,actor['workspace_id'],actor['id'],'peer.created',identifier)
        return {'id':identifier,'token':token,'expires_at':now+7776000,'scope':'catalog:read'}

    def resolve_peer(self,token):
        if not isinstance(token,str) or not token.startswith('cp_peer_') or len(token)>100:return None
        with self.connect(self.url,connect_timeout=5) as db:
            row=db.execute("SELECT p.id,p.registry_id,w.name FROM catproof_peer_tokens p JOIN catproof_members m ON m.registry_id=p.registry_id AND m.user_id=p.creator JOIN catproof_workspaces w ON w.id=p.registry_id WHERE p.token_hash=%s AND p.revoked_at IS NULL AND p.expires_at>%s AND m.role='owner'",(digest(token),time.time())).fetchone()
            if not row:return None
            return {'id':row[0],'email':row[0],'workspace_id':row[1],'workspace_name':row[2],'role':'viewer','auth_type':'peer'}

    def revoke_peer_token(self,actor,identifier,password):
        with self.connect(self.url,connect_timeout=5) as db:
            self.locked(db,actor['workspace_id']);self.owner(db,actor,password)
            row=db.execute('SELECT id FROM catproof_peer_tokens WHERE id=%s AND registry_id=%s',(identifier,actor['workspace_id'])).fetchone()
            if not row:raise ValueError('Peer token not found in this registry.')
            db.execute('UPDATE catproof_peer_tokens SET revoked_at=%s WHERE id=%s',(time.time(),identifier))
            self.event(db,actor['workspace_id'],actor['id'],'peer.revoked',identifier)
