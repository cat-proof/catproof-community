"""Local system of record layered over read-only vendor discovery."""
import csv
import hashlib
import io
import json
import threading
from registry import now

TRACKED = ('name','owner','status','source_status','kind','description')
REVIEWS = ('unreviewed','reviewed','retired')

class ProductStore:
    def __init__(self,database_url,namespace,connect=None,workspace_id='legacy'):
        from federation import FederatedRegistry
        self.namespace=namespace;self.lock=threading.RLock()
        self.db=FederatedRegistry(database_url,env={},connect=connect,workspace_id=workspace_id).db

    def event(self, rid, name, kind, detail):
        self.db.execute('INSERT INTO catproof_events(namespace,registry_id,at,kind,name,detail) VALUES(?,?,?,?,?,?)',
                        (self.namespace,rid,now(),kind,name,json.dumps(detail)))

    def ingest(self, snapshot):
        """Commit a complete discovery result atomically; unsuccessful sources never imply removal."""
        if not isinstance(snapshot,dict) or not isinstance(snapshot.get('agents'),list) or not isinstance(snapshot.get('sources'),list):
            raise ValueError('Invalid inventory response')
        rows = snapshot['agents']
        if any(not all(k in r for k in ('id','source','provenance','last_seen','name','owner','status')) for r in rows):
            raise ValueError('Incomplete inventory response')
        with self.lock, self.db:
            seen = set()
            for row in rows:
                # Do not permit seeded and live identities, or different workspaces, to collide.
                rid = hashlib.sha256((self.namespace+'\0'+row['provenance']+'\0'+row['id']).encode()).hexdigest()[:24]
                if rid in seen:
                    raise ValueError('Duplicate inventory identity')
                seen.add(rid)
                old = self.db.execute('SELECT * FROM catproof_agents WHERE registry_id=?',(rid,)).fetchone()
                if old:
                    before = json.loads(old['payload'])
                    changes = {key:{'before':before.get(key),'after':row.get(key)} for key in TRACKED if before.get(key)!=row.get(key)}
                    if changes:
                        self.event(rid,row['name'],'source_changed',changes)
                    if not old['present']:
                        self.event(rid,row['name'],'rediscovered',{'reason':'Visible in source inventory again'})
                    self.db.execute('UPDATE catproof_agents SET payload=?,present=1 WHERE registry_id=?',(json.dumps(row),rid))
                else:
                    self.db.execute('INSERT INTO catproof_agents(registry_id,namespace,vendor_id,payload,first_seen,present) VALUES(?,?,?,?,?,1)',
                                    (rid,self.namespace,row['id'],json.dumps(row),row['last_seen']))
                    self.event(rid,row['name'],'discovered',{'source':row['source'],'provenance':row['provenance']})
            healthy = {s['name'] for s in snapshot['sources'] if s.get('health')=='ok'}
            for old in self.db.execute('SELECT * FROM catproof_agents WHERE namespace=? AND present=1',(self.namespace,)).fetchall():
                payload = json.loads(old['payload'])
                if old['registry_id'] not in seen and payload['source'] in healthy:
                    self.db.execute('UPDATE catproof_agents SET present=0 WHERE registry_id=?',(old['registry_id'],))
                    self.event(old['registry_id'],payload['name'],'not_observed',{'reason':'Absent from latest successful source inventory; may be deleted or no longer visible'})
            self.db.execute('INSERT INTO catproof_metadata(namespace,sources,synced_at) VALUES(?,?,?) ON CONFLICT(tenant_id,namespace) DO UPDATE SET sources=excluded.sources,synced_at=excluded.synced_at', (self.namespace,json.dumps(snapshot['sources']),now()))
        return self.snapshot()

    def snapshot(self):
        with self.lock:
            meta = self.db.execute('SELECT * FROM catproof_metadata WHERE namespace=?',(self.namespace,)).fetchone()
            agents=[]
            for saved in self.db.execute('SELECT * FROM catproof_agents WHERE namespace=? ORDER BY first_seen,registry_id',(self.namespace,)):
                row=json.loads(saved['payload'])
                row.update(registry_id=saved['registry_id'],first_seen=saved['first_seen'],present=bool(saved['present']),
                           business_owner=saved['business_owner'],review=saved['review'],notes=saved['notes'],reviewed_at=saved['reviewed_at'])
                reasons=[]
                if not row['business_owner']: reasons.append('Business owner missing')
                if row['review']=='unreviewed': reasons.append('Not reviewed')
                if row.get('stale'): reasons.append('Source refresh failed')
                if row['status'] in ('unknown','error'): reasons.append('Source status '+row['status'])
                if not row['present']: reasons.append('No longer observed')
                row['attention']=reasons
                agents.append(row)
            events=[dict(e) for e in self.db.execute('SELECT * FROM catproof_events WHERE namespace=? ORDER BY sequence DESC LIMIT 250',(self.namespace,))]
            for event in events: event['detail']=json.loads(event['detail'])
            return {'agents':agents,'sources':json.loads(meta['sources']) if meta else [],'events':events,
                    'synced_at':meta['synced_at'] if meta else None,'namespace':self.namespace}

    def annotate(self, rid, updates):
        if not isinstance(updates,dict) or set(updates)-{'business_owner','review','notes'}:
            raise ValueError('Unsupported registry fields')
        for key,val in updates.items():
            if not isinstance(val,str) or len(val) > (4000 if key=='notes' else 200):
                raise ValueError('Invalid field value or length')
        if 'review' in updates and updates['review'] not in REVIEWS:
            raise ValueError('Invalid review state')
        with self.lock,self.db:
            old=self.db.execute('SELECT * FROM catproof_agents WHERE registry_id=? AND namespace=?',(rid,self.namespace)).fetchone()
            if not old: raise KeyError('Agent not found')
            changes={k:{'before':old[k],'after':v.strip()} for k,v in updates.items() if old[k]!=v.strip()}
            for key,change in changes.items():
                self.db.execute('UPDATE catproof_agents SET '+key+'=? WHERE registry_id=?',(change['after'],rid))
            if 'review' in changes:
                self.db.execute('UPDATE catproof_agents SET reviewed_at=? WHERE registry_id=?',(now() if updates['review']=='reviewed' else None,rid))
            if changes: self.event(rid,json.loads(old['payload'])['name'],'registry_updated',changes)
        return self.snapshot()

    def export(self, include_seeded=False):
        fields=['registry_id','name','source','provenance','kind','business_owner','owner','status','review','present','stale','first_seen','last_seen','notes']
        out=io.StringIO();writer=csv.writer(out);writer.writerow(fields)
        for row in self.snapshot()['agents']:
            if row['provenance'] != 'live' and not include_seeded:
                continue
            values=[]
            for key in fields:
                val=str(row.get(key,''))
                # Prevent spreadsheet formula injection from vendor metadata or local annotations.
                if val.lstrip().startswith(('=','+','-','@')) or val.startswith(('\t','\r','\n')): val="'"+val
                values.append(val)
            writer.writerow(values)
        return out.getvalue().encode('utf-8-sig')
