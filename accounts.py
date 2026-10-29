"""Password accounts, isolated workspaces and revocable opaque sessions."""
import hashlib
import hmac
import os
import re
import secrets
import time
import uuid

SESSION_SECONDS=28800

def password_hash(password):
    salt=secrets.token_hex(16)
    value=hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),600000).hex()
    return 'pbkdf2_sha256$600000$'+salt+'$'+value

def password_matches(password,encoded):
    try:
        algorithm,iterations,salt,expected=encoded.split('$')
        if algorithm!='pbkdf2_sha256' or int(iterations)!=600000:return False
        value=hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),int(iterations)).hex()
        return hmac.compare_digest(value,expected)
    except (ValueError,TypeError):return False

def validate_signup(data):
    name=data.get('name','').strip();email=data.get('email','').strip().lower();workspace=data.get('workspace','').strip();password=data.get('password','')
    if not 1<=len(name)<=120 or not 1<=len(workspace)<=120:raise ValueError('Enter your name and registry name (up to 120 characters).')
    if len(email)>254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email):raise ValueError('Enter a valid email address.')
    if not 12<=len(password)<=128:raise ValueError('Use a password with 12–128 characters.')
    return name,email,workspace,password

class Accounts:
    def __init__(self,url,connect=None):
        if connect is None:
            import psycopg
            connect=psycopg.connect
        self.url=url;self.connect=connect
        self.dummy=password_hash(secrets.token_urlsafe(24))
        from database import require_hosted_schema
        with self.connect(url,connect_timeout=5) as db:require_hosted_schema(db)
    @staticmethod
    def principal(row):
        return {'id':row[0],'email':row[1],'name':row[2],'workspace_id':row[3],'workspace_name':row[4],'email_verified':False}
    def issue(self,db,user_id):
        token=secrets.token_urlsafe(32)
        db.execute('DELETE FROM catproof_sessions WHERE user_id=%s AND expires_at<%s',(user_id,time.time()))
        db.execute('INSERT INTO catproof_sessions(token_hash,user_id,expires_at) VALUES(%s,%s,%s)',(hashlib.sha256(token.encode()).hexdigest(),user_id,time.time()+SESSION_SECONDS))
        return token
    def register(self,data):
        name,email,workspace,password=validate_signup(data)
        encoded=password_hash(password);uid='u_'+uuid.uuid4().hex;wid='ws_'+uuid.uuid4().hex
        try:
            with self.connect(self.url,connect_timeout=5) as db:
                db.execute('INSERT INTO catproof_workspaces(id,name,created_at) VALUES(%s,%s,%s)',(wid,workspace,time.time()))
                db.execute('INSERT INTO catproof_users(id,email,name,workspace_id,password_hash,created_at) VALUES(%s,%s,%s,%s,%s,%s)',(uid,email,name,wid,encoded,time.time()))
                token=self.issue(db,uid)
        except Exception as exc:
            if getattr(exc,'sqlstate',None)=='23505' or exc.__class__.__name__=='IntegrityError':raise ValueError('An account already uses this email. Sign in instead.') from None
            raise
        return {'id':uid,'email':email,'name':name,'workspace_id':wid,'workspace_name':workspace,'email_verified':False},token
    def login(self,email,password):
        email=email.strip().lower()
        with self.connect(self.url,connect_timeout=5) as db:
            # Keep verification and issuance serialized with password changes and
            # recovery. NO KEY UPDATE also permits unrelated FK checks on users.
            row=db.execute('SELECT u.id,u.email,u.name,u.workspace_id,w.name,u.password_hash FROM catproof_users u JOIN catproof_workspaces w ON w.id=u.workspace_id WHERE u.email=%s FOR NO KEY UPDATE OF u',(email,)).fetchone()
            valid=password_matches(password,row[5] if row else self.dummy)
            if not row or not valid:return None
            return self.principal(row),self.issue(db,row[0])
    def legacy_login(self,user):
        # Only invoked after the existing bootstrap password is verified by the handler.
        uid='legacy_'+user
        with self.connect(self.url,connect_timeout=5) as db:
            db.execute('INSERT INTO catproof_users(id,email,name,workspace_id,password_hash,created_at) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING',(uid,user,user,'legacy','bootstrap-only',time.time()))
            return {'id':uid,'email':user,'name':user,'workspace_id':'legacy','workspace_name':'CatProof team','email_verified':False},self.issue(db,uid)
    def resolve(self,token):
        if not token or len(token)>100:return None
        with self.connect(self.url,connect_timeout=5) as db:
            row=db.execute('SELECT u.id,u.email,u.name,u.workspace_id,w.name FROM catproof_sessions s JOIN catproof_users u ON u.id=s.user_id JOIN catproof_workspaces w ON w.id=u.workspace_id WHERE s.token_hash=%s AND s.expires_at>%s',(hashlib.sha256(token.encode()).hexdigest(),time.time())).fetchone()
            return self.principal(row) if row else None
    def revoke(self,token):
        with self.connect(self.url,connect_timeout=5) as db:db.execute('DELETE FROM catproof_sessions WHERE token_hash=%s',(hashlib.sha256(token.encode()).hexdigest(),))
    def change_password(self,user_id,current,new):
        if not 12<=len(new)<=128:raise ValueError('Use a password with 12–128 characters.')
        with self.connect(self.url,connect_timeout=5) as db:
            row=db.execute('SELECT password_hash FROM catproof_users WHERE id=%s FOR NO KEY UPDATE',(user_id,)).fetchone()
            if not row or not password_matches(current,row[0]):raise ValueError('Current password is incorrect.')
            db.execute('UPDATE catproof_users SET password_hash=%s WHERE id=%s',(password_hash(new),user_id))
            db.execute('DELETE FROM catproof_sessions WHERE user_id=%s',(user_id,))
            return self.issue(db,user_id)
