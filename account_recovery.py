"""Operator-provisioned, account-bound, expiring recovery grants. No public issuance."""
import hashlib
import json
import re
import secrets
import time
from accounts import password_hash

class AccountRecovery:
    def __init__(self,accounts,configuration=''):
        self.a=accounts
        with accounts.connect(accounts.url,connect_timeout=5) as db:
            db.execute('SELECT pg_advisory_xact_lock(7102770)')
            if configuration:
                grants=json.loads(configuration)
                if not isinstance(grants,list) or len(grants)>10:raise ValueError('Invalid recovery grant configuration')
                for g in grants:
                    if set(g)!={'email','token_hash','expires_at'} or not re.fullmatch('[a-f0-9]{64}',g['token_hash']) or type(g['expires_at']) not in (int,float):raise ValueError('Invalid recovery grant configuration')
                    # Absolute expiry and INSERT-only semantics prevent restart/redeploy from rearming a used grant.
                    if not time.time()<g['expires_at']<=time.time()+86400:continue
                    row=db.execute('SELECT id FROM catproof_users WHERE email=%s',(g['email'].strip().lower(),)).fetchone()
                    if not row:
                        print('Recovery grant not provisioned: target account not found',flush=True);continue
                    db.execute('INSERT INTO catproof_account_recovery(token_hash,user_id,expires_at) VALUES(%s,%s,%s) ON CONFLICT(token_hash) DO NOTHING',(g['token_hash'],row[0],g['expires_at']))
    def issue(self,email):
        token=secrets.token_urlsafe(32);now=time.time()
        with self.a.connect(self.a.url,connect_timeout=5) as db:
            db.execute('SELECT pg_advisory_xact_lock(7102770)')
            row=db.execute('SELECT id,workspace_id FROM catproof_users WHERE email=%s',(email.strip().lower(),)).fetchone()
            if not row:return None
            db.execute('DELETE FROM catproof_account_recovery WHERE expires_at<%s',(now,))
            db.execute('INSERT INTO catproof_account_recovery(token_hash,user_id,expires_at) VALUES(%s,%s,%s)',(hashlib.sha256(token.encode()).hexdigest(),row[0],now+1800))
            self.a.event(db,row[1],row[0],'account.password_reset_requested')
        return token
    def reset(self,email,token,password):
        if not isinstance(password,str) or not 12<=len(password)<=128:raise ValueError('Use a password with 12–128 characters.')
        if not isinstance(token,str) or not re.fullmatch('[A-Za-z0-9_-]{43,100}',token):raise PermissionError('Recovery link expired, used or invalid. Contact the service owner.')
        encoded=password_hash(password);now=time.time();digest=hashlib.sha256(token.encode()).hexdigest()
        with self.a.connect(self.a.url,connect_timeout=5) as db:
            db.execute('SELECT pg_advisory_xact_lock(7102770)')
            row=db.execute('SELECT r.user_id,u.workspace_id FROM catproof_account_recovery r JOIN catproof_users u ON u.id=r.user_id WHERE r.token_hash=%s AND r.used_at IS NULL AND r.expires_at>%s AND u.email=%s FOR NO KEY UPDATE OF u',(digest,now,email.strip().lower())).fetchone()
            if not row:raise PermissionError('Recovery link expired, used or invalid. Contact the service owner.')
            db.execute('UPDATE catproof_users SET password_hash=%s WHERE id=%s',(encoded,row[0]))
            db.execute('UPDATE catproof_account_recovery SET used_at=%s WHERE user_id=%s AND used_at IS NULL',(now,row[0]))
            db.execute('DELETE FROM catproof_sessions WHERE user_id=%s',(row[0],))
            self.a.event(db,row[1],row[0],'account.password_recovered')
