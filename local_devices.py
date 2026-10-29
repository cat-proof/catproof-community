"""Metadata-only local collectors. Device tokens can report, never read registry data."""
import json
import re
import secrets
import time
import uuid
from enterprise import digest

EVENTS={'SessionStart','SessionEnd','UserPromptSubmit','Stop'}
class SlowReport(Exception):pass

def metadata(data):
    if not isinstance(data,dict) or set(data)!={'schema','sequence','platform','tools','sessions'} or data['schema']!='catproof.local.v1':raise ValueError('Unsupported local report schema')
    if type(data['sequence']) is not int or not 0<data['sequence']<2**53:raise ValueError('Invalid report sequence')
    if data['platform'] not in ('macOS','Linux','Windows','Other'):raise ValueError('Unsupported platform')
    tools=data['tools'];sessions=data['sessions']
    if not isinstance(tools,list) or len(tools)>2 or not isinstance(sessions,list) or len(sessions)>20:raise ValueError('Inventory limit exceeded')
    seen=set()
    for t in tools:
        if not isinstance(t,dict) or set(t)!={'tool','installed','version','agents','mcp_servers'} or t['tool'] not in ('codex','claude') or t['tool'] in seen:raise ValueError('Invalid tool metadata')
        seen.add(t['tool'])
        if type(t['installed']) is not bool or not isinstance(t['version'],str) or not re.fullmatch(r'[0-9A-Za-z.+_-]{0,40}',t['version']):raise ValueError('Invalid tool version')
        for key in ('agents','mcp_servers'):
            names=t[key]
            if not isinstance(names,list) or len(names)>50 or len(set(x for x in names if isinstance(x,str)))!=len(names) or any(not isinstance(n,str) or not re.fullmatch(r'[A-Za-z0-9 _./:-]{1,100}',n) for n in names):raise ValueError('Invalid configured resource names')
    ids=set()
    for s in sessions:
        if not isinstance(s,dict) or set(s)!={'id','tool','event','at'} or not re.fullmatch('[a-f0-9]{64}',str(s['id'])) or s['id'] in ids or s['tool'] not in ('codex','claude') or s['event'] not in EVENTS:raise ValueError('Invalid activity metadata')
        ids.add(s['id'])
        if type(s['at']) not in (int,float) or not time.time()-86400<=s['at']<=time.time()+300:raise ValueError('Invalid activity timestamp')
    return json.dumps(data,separators=(',',':'))

class LocalDevices:
    def __init__(self,accounts):
        self.a=accounts
    def member(self,db,actor):
        if not db.execute('SELECT 1 FROM catproof_members WHERE registry_id=%s AND user_id=%s',(actor['workspace_id'],actor['id'])).fetchone():raise PermissionError('Registry membership changed')
    def pair(self,actor,label,password):
        if not isinstance(label,str) or not 1<=len(label.strip())<=100 or any(ord(c)<32 for c in label):raise ValueError('Enter a device label (1–100 characters)')
        now=time.time();code=secrets.token_urlsafe(32);pid='pair_'+uuid.uuid4().hex
        with self.a.connect(self.a.url,connect_timeout=5) as db:
            self.a.locked(db,actor['workspace_id']);self.a.owner(db,actor,password)
            db.execute('DELETE FROM catproof_local_pairings WHERE expires_at<%s OR used_at IS NOT NULL',(now,))
            count=db.execute('SELECT count(*) FROM catproof_local_pairings WHERE registry_id=%s',(actor['workspace_id'],)).fetchone()[0]
            if count>=20:raise ValueError('Too many pending enrollments; wait ten minutes')
            db.execute('INSERT INTO catproof_local_pairings(id,registry_id,creator,owner_email,label,code_hash,expires_at) VALUES(%s,%s,%s,%s,%s,%s,%s)',(pid,actor['workspace_id'],actor['id'],actor['email'],label.strip(),digest(code),now+600))
            self.a.event(db,actor['workspace_id'],actor['id'],'local.pairing_created',pid)
        return {'id':pid,'code':code,'expires_at':now+600}
    def enroll(self,code):
        if not isinstance(code,str) or not 30<=len(code)<=100:raise PermissionError('Invalid or expired enrollment code')
        now=time.time();token=secrets.token_urlsafe(32);did='device_'+uuid.uuid4().hex
        with self.a.connect(self.a.url,connect_timeout=5) as db:
            target=db.execute('SELECT registry_id FROM catproof_local_pairings WHERE code_hash=%s',(digest(code),)).fetchone()
            if not target:raise PermissionError('Invalid or expired enrollment code')
            self.a.locked(db,target[0])
            row=db.execute("SELECT p.id,p.registry_id,p.owner_email,p.label FROM catproof_local_pairings p JOIN catproof_members m ON m.registry_id=p.registry_id AND m.user_id=p.creator WHERE p.code_hash=%s AND p.used_at IS NULL AND p.expires_at>%s AND m.role='owner'",(digest(code),now)).fetchone()
            if not row:raise PermissionError('Invalid or expired enrollment code')
            count=db.execute('SELECT count(*) FROM catproof_local_devices WHERE registry_id=%s',(row[1],)).fetchone()[0]
            if count>=100:raise ValueError('Pilot limit: 100 devices per registry')
            db.execute('INSERT INTO catproof_local_devices(id,registry_id,owner_email,label,token_hash,created_at,expires_at,payload) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',(did,row[1],row[2],row[3],digest(token),now,now+7776000,'{}'))
            db.execute('UPDATE catproof_local_pairings SET used_at=%s WHERE id=%s',(now,row[0]))
            self.a.event(db,row[1],row[2],'local.enrolled',did)
        return {'device_id':did,'token':token,'expires_at':now+7776000}
    def report(self,token,data):
        if not isinstance(token,str) or not 30<=len(token)<=100:raise PermissionError('Invalid device token')
        payload=metadata(data);now=time.time()
        with self.a.connect(self.a.url,connect_timeout=5) as db:
            target=db.execute('SELECT registry_id FROM catproof_local_devices WHERE token_hash=%s',(digest(token),)).fetchone()
            if not target:raise PermissionError('Invalid device token')
            self.a.locked(db,target[0])
            row=db.execute('SELECT id,registry_id,last_seen,sequence,payload FROM catproof_local_devices WHERE token_hash=%s AND revoked_at IS NULL AND expires_at>%s',(digest(token),now)).fetchone()
            if not row:raise PermissionError('Device token expired or revoked')
            if data['sequence']<=row[3]:raise ValueError('Stale or repeated report sequence')
            if row[2] is not None and now-row[2]<30:raise SlowReport()
            old=json.loads(row[4]);old_tools=old.get('tools',[])
            if old_tools!=data['tools']:self.a.event(db,row[1],row[0],'local.inventory_changed',row[0])
            db.execute('UPDATE catproof_local_devices SET payload=%s,last_seen=%s,sequence=%s WHERE id=%s',(payload,now,data['sequence'],row[0]))
        return {'ok':True,'next_report_seconds':60}
    def listing(self,actor):
        now=time.time()
        with self.a.connect(self.a.url,connect_timeout=5) as db:
            self.member(db,actor)
            rows=db.execute('SELECT id,owner_email,label,created_at,expires_at,revoked_at,last_seen,payload FROM catproof_local_devices WHERE registry_id=%s ORDER BY created_at DESC',(actor['workspace_id'],)).fetchall()
            pending=[]
            if actor['role']=='owner':pending=[dict(zip(('id','label','expires_at'),r)) for r in db.execute('SELECT id,label,expires_at FROM catproof_local_pairings WHERE registry_id=%s AND expires_at>%s AND used_at IS NULL',(actor['workspace_id'],now)).fetchall()]
        devices=[]
        for r in rows:
            item=dict(zip(('id','owner','label','created_at','expires_at','revoked_at','last_seen'),r[:7]));item['inventory']=json.loads(r[7]);item['status']='revoked' if r[5] else 'expired' if r[4]<=now else 'awaiting first report' if r[6] is None else 'reporting' if now-r[6]<=180 else 'offline';devices.append(item)
        return {'devices':devices,'pending':pending,'role':actor['role'],'presence_timeout_seconds':180,'limitation':'Self-reported installation and activity metadata. Presence does not prove an agent is running or trusted.'}
    def revoke(self,actor,identifier,password,pairing=False):
        with self.a.connect(self.a.url,connect_timeout=5) as db:
            self.a.locked(db,actor['workspace_id']);self.a.owner(db,actor,password)
            table='catproof_local_pairings' if pairing else 'catproof_local_devices'
            if not db.execute('SELECT 1 FROM '+table+' WHERE id=%s AND registry_id=%s',(identifier,actor['workspace_id'])).fetchone():raise ValueError('Device or enrollment not found')
            if pairing:db.execute('DELETE FROM catproof_local_pairings WHERE id=%s',(identifier,))
            else:db.execute('UPDATE catproof_local_devices SET revoked_at=%s WHERE id=%s',(time.time(),identifier))
            self.a.event(db,actor['workspace_id'],actor['id'],'local.pairing_cancelled' if pairing else 'local.revoked',identifier)
