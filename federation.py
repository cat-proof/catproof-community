from network_policy import build_opener
"""Canonical agent registry and independent, attributable federated source observations."""
import csv
import hashlib
import io
import json
import os
import re
import threading
import uuid
from urllib.parse import urlsplit
from urllib.request import Request
from registry import ConnectorError, NoRedirect, databricks, servicenow, seed, now
from adapters import CATALOG, validate_config, read_connector
from adapters.auth import SUFFIXES,auth_spec,OPTIONAL

class DuplicateConnection(ValueError):
    def __init__(self,identifier):
        super().__init__('This connection is already saved. Open it to check setup and sync; saving a connection does not import projects.')
        self.connection_id=identifier

COMMUNITY_CONNECTION_LIMIT=2

class ConnectionLimitError(ValueError):
    def __init__(self,maximum=COMMUNITY_CONNECTION_LIMIT):
        super().__init__(f'Community Edition supports up to {maximum} saved source connections per registry. Paused connections count. Archive a connection to free a slot; its records and history are preserved.')
        self.maximum=maximum

def connection_limit_response(exc):
    return {'error':str(exc),'error_code':'community_connection_limit','maximum_connections':exc.maximum}

LIFECYCLES=('draft','active','suspended','retired')
KINDS=('discovery','atlas','databricks','servicenow','seeded')+tuple(CATALOG)
TRACKED=('name','owner','status','source_status','kind','description','evidence')

def identity(connection, record):
    return 'obs_'+hashlib.sha256((connection+'\0'+record['provenance']+'\0'+record['id']).encode()).hexdigest()[:24]

def validate_origin(url, local_port=None):
    parsed=urlsplit(url)
    local=parsed.hostname in ('localhost','127.0.0.1')
    if parsed.scheme!='https' and not (parsed.scheme=='http' and local):
        raise ValueError('HTTPS is required except for local registry connections')
    if not parsed.hostname or parsed.username or parsed.password or parsed.path not in ('','/') or parsed.query or parsed.fragment:
        raise ValueError('Use an origin without a path, credentials, query or fragment')
    if local and local_port and parsed.port==local_port:raise ValueError('A registry cannot connect to itself')
    return url.rstrip('/')

def validate_rows(rows):
    if not isinstance(rows,list) or len(rows)>10000:raise ValueError('Invalid or oversized inventory')
    seen=set()
    for row in rows:
        if not isinstance(row,dict):raise ValueError('Invalid source record')
        for key in ('id','source','provenance','name','owner','status','last_seen'):
            if not isinstance(row.get(key),str) or (key in ('id','source','name') and not row[key]):raise ValueError('Missing normalized source field')
        if row['provenance'] not in ('live','seeded','manual'):raise ValueError('Invalid source provenance')
        for key in ('stale', 'present'):
            if key in row and type(row[key]) is not bool:raise ValueError('Invalid observation freshness')
        key=(row['provenance'],row['id'])
        if key in seen:raise ValueError('Duplicate source identity')
        seen.add(key)
    return rows

def validate_assets(rows):
    if not isinstance(rows,list) or len(rows)>10000:raise ValueError('Invalid or oversized asset inventory')
    seen=set()
    for row in rows:
        if not isinstance(row,dict) or any(not isinstance(row.get(k),str) or not row[k].strip() for k in ('id','name','source','resource_type','provenance','last_seen')):raise ValueError('Invalid asset inventory')
        if row['provenance'] not in ('live','seeded','manual'):raise ValueError('Invalid asset provenance')
        for key in ('stale','present'):
            if key in row and type(row[key]) is not bool:raise ValueError('Invalid asset freshness')
        if row['id'] in seen:raise ValueError('Duplicate asset identity')
        seen.add(row['id'])
    return rows

def discover(connection, env, refresh=False):
    kind=connection['kind'];prefix=connection['credential_prefix']
    if kind in CATALOG:return read_connector(connection,env)
    if kind=='seeded':
        return {'agents':seed('ServiceNow'),'sources':[{'name':'ServiceNow','mode':'seeded','health':'ok'}]}
    if kind in ('databricks','servicenow'):
        if kind=='databricks':
            token=env.get(prefix+'_TOKEN')
            if not token:raise ConnectorError('Access token missing. Open this connection and choose Configure credentials.')
            rows=databricks({'DATABRICKS_HOST':connection['url'],'DATABRICKS_TOKEN':token})
            source='Databricks'
        else:
            token=env.get(prefix+'_TOKEN');user=env.get(prefix+'_USERNAME');password=env.get(prefix+'_PASSWORD')
            if not token and not (user and password):raise ConnectorError('ServiceNow credentials missing. Open this connection and choose Configure credentials.')
            cfg={'SERVICENOW_INSTANCE_URL':connection['url'],'SERVICENOW_TOKEN':token or '',
                 'SERVICENOW_USERNAME':user or '', 'SERVICENOW_PASSWORD':password or '',
                 'SERVICENOW_OWNER_FIELD':env.get(prefix+'_OWNER_FIELD','owned_by')}
            rows=servicenow(cfg);source='ServiceNow'
        return {'agents':rows,'sources':[{'name':source,'mode':'live','health':'ok'}]}
    path='/api/catalog' if kind=='atlas' else '/api/inventory'
    # Remote federation reads catalogs. Explicit refresh may re-read a LOCAL discovery bridge.
    local=urlsplit(connection['url']).hostname in ('127.0.0.1','localhost')
    local_refresh=kind=='discovery' and local and refresh
    if local_refresh:path='/api/refresh'
    headers={'Accept':'application/json'}
    if local_refresh:headers['X-Registry-Action']='refresh'
    token=env.get(prefix+'_TOKEN') if prefix else None
    if token:headers['Authorization']='Bearer '+token
    request=Request(connection['url']+path,method='POST' if local_refresh else 'GET',headers=headers)
    try:
        with build_opener(NoRedirect).open(request,timeout=120 if local_refresh else 15) as response:
            body=response.read(20_000_001)
            if len(body)>20_000_000:raise ValueError('Response too large')
            payload=json.loads(body)
    except Exception:raise ConnectorError('Registry read failed. Check URL, access and connector availability.') from None
    if not isinstance(payload,dict) or 'error' in payload or payload.get('errors'):raise ValueError('Invalid or incomplete registry response')
    if kind=='atlas' and payload.get('schema_version')!='atlas.registry.v1':raise ValueError('Unsupported peer registry schema version')
    rows=payload.get('agents')
    validate_rows(rows)
    sources=payload.get('sources',[])
    if not isinstance(sources,list) or any(not isinstance(s,dict) for s in sources):raise ValueError('Invalid source health response')
    wanted=connection['source_filter']
    if wanted:
        rows=[r for r in rows if r['source']==wanted]
        sources=[s for s in sources if s.get('name')==wanted]
        if not sources and not rows:raise ConnectorError('Configured source filter was not found in the upstream registry.')
    evidence_contract = kind == 'atlas' and payload.get('evidence_version') == 1
    if any(s.get('health') not in (('ok','degraded') if evidence_contract else ('ok',)) for s in sources):raise ConnectorError('Upstream source reported an error; last successful observations retained.')
    if evidence_contract:
        for row in rows:
            if type(row.get('stale')) is not bool or type(row.get('present')) is not bool:
                raise ConnectorError('Peer omitted freshness evidence; previous observations retained.')
    elif kind == 'atlas':
        # Older peers cannot establish source freshness, even if their HTTP read succeeds.
        rows = [dict(row, stale=True) for row in rows]
    return {'agents':rows,'sources':sources}

class FederatedRegistry:
    def __init__(self,database_url=None,env=None,reader=discover,local_port=8770,workspace_id='legacy',connect=None,db=None,connection_limit=COMMUNITY_CONNECTION_LIMIT):
        self.actor=''
        self.workspace_id=workspace_id;self.connection_limit=connection_limit
        self.env=dict(os.environ if env is None else env);self.reader=reader;self.local_port=local_port
        self.lock=threading.RLock();self.refresh_lock=threading.Lock()
        from postgres_registry import RegistryConnection
        if db is None:
            import psycopg
            from database import require_hosted_schema
            connect=connect or psycopg.connect
            raw=connect(database_url or os.environ['DATABASE_URL'])
            try:
                require_hosted_schema(raw);raw.commit();raw.autocommit=True
                self.db=RegistryConnection(raw,workspace_id,owned=True)
            except BaseException:
                raw.close();raise
        else:self.db=RegistryConnection(db,workspace_id)

    def event(self,kind,name,detail,entity=None,connection=None):
        self.db.execute('INSERT INTO catproof_registry_events(entity_id,connection_id,at,kind,name,detail,actor) VALUES(?,?,?,?,?,?,?)',
                        (entity,connection,now(),kind,name,json.dumps(detail),self.actor))

    def configured_connection_ids(self):
        return [row['id'] for row in self.db.execute('SELECT id FROM catproof_connections WHERE archived=0 ORDER BY created_at,id')]

    def available_connection_ids(self):
        ids=self.configured_connection_ids()
        return ids if self.connection_limit is None else ids[:self.connection_limit]

    def require_connection_available(self,cid):
        row=self.db.execute('SELECT archived FROM catproof_connections WHERE id=?',(cid,)).fetchone()
        if not row:raise KeyError('Connection not found')
        if row['archived']:raise ValueError('Restore this archived connection before using it.')
        if cid not in self.available_connection_ids():raise ConnectionLimitError(self.connection_limit)

    def reserve_connection_slot(self):
        # Shared with hosted writes and membership changes, across processes.
        from registry_locks import lock_registry
        lock_registry(self.db,self.workspace_id)
        if self.connection_limit is not None and len(self.configured_connection_ids())>=self.connection_limit:
            raise ConnectionLimitError(self.connection_limit)

    def add_connection(self,data):
        if not isinstance(data,dict) or set(data)-{'name','kind','url','credential_prefix','source_filter','config'}:raise ValueError('Unsupported connection fields')
        kind=data.get('kind');name=data.get('name','').strip()
        if kind not in KINDS or not name or len(name)>120:raise ValueError('Invalid connection name or adapter')
        prefix=data.get('credential_prefix','').strip()
        if prefix and not re.fullmatch('[A-Z][A-Z0-9_]{0,79}',prefix):raise ValueError('Use an uppercase environment variable prefix')
        if kind not in ('discovery','atlas','seeded','a2a','mcp') and not prefix:raise ValueError('A credential environment prefix is required')
        config=validate_config(kind,data.get('config',{}))
        from adapters.catalog import ACCOUNT_FIELDS
        scope=ACCOUNT_FIELDS.get(kind)
        if scope and not config.get(scope):raise ValueError('Provide '+scope+' to identify this account separately from other connections')
        if scope=='account_id' and not re.fullmatch(r'[0-9]{12}',config[scope]):raise ValueError('AWS account ID must contain 12 digits')
        if scope=='tenant_id' and not re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}',config[scope]):raise ValueError('Tenant ID must be a UUID')
        url=validate_origin(data.get('url',''),self.local_port) if kind!='seeded' else ''
        if kind=='lovable' and url!='https://api.lovable.dev':raise ValueError('Lovable requires https://api.lovable.dev')
        source_filter=data.get('source_filter','').strip()
        if source_filter and kind not in ('atlas','discovery'):raise ValueError('Source filtering is only supported by registry federation connectors')
        if len(source_filter)>120:raise ValueError('Source filter too long')
        with self.lock,self.db:
            from registry_locks import lock_registry
            lock_registry(self.db,self.workspace_id)
            duplicate=self.db.execute('SELECT id FROM catproof_connections WHERE kind=? AND url=? AND source_filter=? AND config=?',(kind,url,source_filter,json.dumps(config,sort_keys=True))).fetchone()
            if duplicate:raise DuplicateConnection(duplicate['id'])
            self.reserve_connection_slot()
            cid='conn_'+uuid.uuid4().hex[:16]
            self.db.execute('INSERT INTO catproof_connections(id,name,kind,url,credential_prefix,source_filter,created_at,config) VALUES(?,?,?,?,?,?,?,?)',
                            (cid,name,kind,url,prefix,source_filter,now(),json.dumps(config,sort_keys=True)))
            self.event('connection_added',name,{'adapter':kind,'url':url},connection=cid)
        return cid

    def set_enabled(self,cid,enabled):
        if not isinstance(enabled,bool):raise ValueError('Enabled must be a boolean')
        with self.lock,self.db:
            conn=self.db.execute('SELECT * FROM catproof_connections WHERE id=?',(cid,)).fetchone()
            if not conn:raise KeyError('Connection not found')
            if enabled:self.require_connection_available(cid)
            self.db.execute('UPDATE catproof_connections SET enabled=?,sync_generation=? WHERE id=?',(int(enabled),uuid.uuid4().hex,cid))
            if not enabled:
                self.db.execute('UPDATE catproof_observations SET stale=1 WHERE connection_id=?',(cid,))
                self.db.execute('UPDATE catproof_assets SET stale=1 WHERE connection_id=?',(cid,))
            self.event('connection_enabled' if enabled else 'connection_paused',conn['name'],{},connection=cid)

    def set_archived(self,cid,archived):
        if not isinstance(archived,bool):raise ValueError('Archived must be a boolean')
        from registry_locks import lock_registry
        with self.lock,self.db:
            lock_registry(self.db,self.workspace_id)
            conn=self.db.execute('SELECT * FROM catproof_connections WHERE id=?',(cid,)).fetchone()
            if not conn:raise KeyError('Connection not found')
            if bool(conn['archived'])==archived:return
            if not archived:self.reserve_connection_slot()
            self.db.execute('UPDATE catproof_connections SET archived=?,enabled=0,sync_generation=? WHERE id=?',(int(archived),uuid.uuid4().hex,cid))
            self.db.execute('UPDATE catproof_observations SET stale=1 WHERE connection_id=?',(cid,))
            self.db.execute('UPDATE catproof_assets SET stale=1 WHERE connection_id=?',(cid,))
            self.event('connection_archived' if archived else 'connection_restored',conn['name'],{},connection=cid)

    def ingest(self,cid,payload):
        if not isinstance(payload,dict) or 'error' in payload or payload.get('errors'):raise ValueError('Invalid or incomplete inventory')
        rows=validate_rows(payload.get('agents'))
        asset_rows=validate_assets(payload.get('assets',[]))
        if len(rows)+len(asset_rows)>10000:raise ValueError('Inventory exceeds the 10,000 record limit')
        with self.lock,self.db:
            conn=self.db.execute('SELECT * FROM catproof_connections WHERE id=?',(cid,)).fetchone()
            if not conn:raise KeyError('Connection not found')
            seen=set()
            for row in rows:
                oid=identity(cid,row);seen.add(oid)
                old=self.db.execute('SELECT * FROM catproof_observations WHERE id=?',(oid,)).fetchone()
                if old:
                    entity=self.db.execute('SELECT entity_id FROM catproof_bindings WHERE observation_id=?',(oid,)).fetchone()['entity_id']
                    before=json.loads(old['payload']);changes={k:{'before':before.get(k),'after':row.get(k)} for k in TRACKED if before.get(k)!=row.get(k)}
                    if changes:self.event('source_changed',row['name'],changes,entity,cid)
                    if not old['present'] and row.get('present',True):self.event('rediscovered',row['name'],{'observation_id':oid},entity,cid)
                    if old['present'] and not row.get('present',True):self.event('not_observed',row['name'],{'observation_id':oid,'reason':'Peer reports the source identity is no longer observed'},entity,cid)
                    self.db.execute('UPDATE catproof_observations SET payload=?,present=?,stale=? WHERE id=?',(json.dumps(row),int(row.get('present',True)),int(row.get('stale',False)),oid))
                else:
                    eid=self._create({'name':row['name'][:200],'purpose':row.get('description','')[:8000]},manual=2 if row['provenance']=='seeded' else 0)
                    self.db.execute('INSERT INTO catproof_observations(id,connection_id,payload,first_seen,present,stale) VALUES(?,?,?,?,?,?)',(oid,cid,json.dumps(row),row['last_seen'],int(row.get('present',True)),int(row.get('stale',False))))
                    self.db.execute('INSERT INTO catproof_bindings(observation_id,entity_id) VALUES(?,?)',(oid,eid))
                    self.event('discovered',row['name'],{'source':row['source'],'source_id':row.get('source_id',row['id']),'observation_id':oid},eid,cid)
            for old in self.db.execute('SELECT o.*,b.entity_id FROM catproof_observations o JOIN catproof_bindings b ON b.observation_id=o.id WHERE o.connection_id=? AND o.present=1',(cid,)).fetchall():
                if old['id'] not in seen:
                    self.db.execute('UPDATE catproof_observations SET present=0 WHERE id=?',(old['id'],))
                    self.event('not_observed',json.loads(old['payload'])['name'],{'observation_id':old['id'],'reason':'Absent from latest successful registry read; deletion is not confirmed'},old['entity_id'],cid)
            asset_ids=set()
            for a in asset_rows:
                aid='asset_'+hashlib.sha256((cid+'\0'+a['id']).encode()).hexdigest()[:24];asset_ids.add(aid)
                old=self.db.execute('SELECT * FROM catproof_assets WHERE id=?',(aid,)).fetchone()
                if old:
                    before=json.loads(old['payload'])
                    if before['provenance']!=a['provenance']:raise ValueError('Asset provenance changed; use a separate connection for fictional and real inventory')
                    changes={k:{'before':before.get(k),'after':a.get(k)} for k in ('name','description','evidence') if before.get(k)!=a.get(k)}
                    if changes:self.event('asset_changed',a['name'],{'asset_id':aid,'changes':changes},connection=cid)
                    self.db.execute('UPDATE catproof_assets SET payload=?,present=?,stale=? WHERE id=?',(json.dumps(a),int(a.get('present',True)),int(a.get('stale',False)),aid))
                else:
                    self.db.execute('INSERT INTO catproof_assets(id,connection_id,payload,first_seen,present,stale) VALUES(?,?,?,?,?,?)',(aid,cid,json.dumps(a),a['last_seen'],int(a.get('present',True)),int(a.get('stale',False))))
                    self.event('asset_discovered',a['name'],{'asset_id':aid,'resource_type':a['resource_type']},connection=cid)
            for old in self.db.execute('SELECT id FROM catproof_assets WHERE connection_id=?',(cid,)).fetchall():
                if old['id'] not in asset_ids:self.db.execute('UPDATE catproof_assets SET present=0 WHERE id=?',(old['id'],))
            modes={r['provenance'] for r in rows+asset_rows}
            source_modes={s.get('mode') for s in payload.get('sources',[]) if isinstance(s,dict)}
            mode='seeded' if conn['kind']=='seeded' or modes=={'seeded'} or (not modes and source_modes=={'seeded'}) else 'mixed' if 'seeded' in modes else 'live'
            self.db.execute('UPDATE catproof_connections SET last_success=?,error=NULL,mode=? WHERE id=?',(now(),mode,cid))

    def has_credentials(self,connection):
        env=self.credential_env(connection);prefix=connection['credential_prefix']
        if connection['kind'].startswith('aws_'):return bool(env.get(prefix+'_ACCESS_KEY_ID') and env.get(prefix+'_SECRET_ACCESS_KEY') or env.get(prefix+'_PROFILE'))
        if connection['kind']=='servicenow':return bool(env.get(prefix+'_TOKEN') or env.get(prefix+'_USERNAME') and env.get(prefix+'_PASSWORD'))
        return bool(env.get(prefix+'_TOKEN'))

    def credential_env(self,connection):
        env=dict(self.env)
        token=getattr(self,'credentials',{}).get(connection['id'])
        if token is not None:
            prefix=connection['credential_prefix']
            for suffix in SUFFIXES:env.pop(prefix+'_'+suffix,None)
            if isinstance(token,str):env[prefix+'_TOKEN']=token
            else:
                for suffix,value in token.items():env[prefix+'_'+suffix]=value
        return env

    def refresh(self,cid=None,force=True):
        with self.refresh_lock:
            with self.lock:
                if cid:self.require_connection_available(cid)
                conns=[dict(c) for c in self.db.execute('SELECT * FROM catproof_connections WHERE enabled=1'+(' AND id=?' if cid else ''),(cid,) if cid else ())]
                available=set(self.available_connection_ids())
                conns=[c for c in conns if c['id'] in available]
            if cid and not conns:raise ValueError('Connection is missing or paused')
            for conn in conns:
                started=now();error=None;payload=None
                with self.lock,self.db:self.db.execute('UPDATE catproof_connections SET last_attempt=? WHERE id=?',(started,conn['id']))
                try:
                    payload=self.reader(conn,self.credential_env(conn),force)
                except Exception as exc:
                    error=str(exc) if isinstance(exc,ConnectorError) else 'Registry response was invalid. Previous observations retained.'
                self.apply_refresh(conn,payload,error,started)
        return self.snapshot()

    def apply_refresh(self,conn,payload,error,started):
        with self.lock:
            if conn['id'] not in self.available_connection_ids():return
        count=0
        if error is None:
            try:
                count=len(validate_rows(payload.get('agents')))+len(payload.get('assets',[]))
                self.ingest(conn['id'],payload)
            except Exception:
                error='Registry response was invalid. Previous observations retained.'
        with self.lock,self.db:
            if error:
                self.db.execute('UPDATE catproof_connections SET error=? WHERE id=?',(error,conn['id']))
                self.db.execute('UPDATE catproof_observations SET stale=1 WHERE connection_id=?',(conn['id'],))
                self.db.execute('UPDATE catproof_assets SET stale=1 WHERE connection_id=?',(conn['id'],))
                self.event('sync_failed',conn['name'],{'error':error},connection=conn['id'])
            self.db.execute('INSERT INTO catproof_sync_runs(id,connection_id,started_at,completed_at,outcome,observed,error) VALUES(?,?,?,?,?,?,?)',('run_'+uuid.uuid4().hex[:16],conn['id'],started,now(),'failed' if error else 'succeeded',count if not error else 0,error))

    def _validate_entity(self,data,create=False):
        allowed={'name','purpose','business_owner','lifecycle','review','notes'}
        if not isinstance(data,dict) or set(data)-allowed:raise ValueError('Unsupported canonical fields')
        for key,val in data.items():
            if not isinstance(val,str) or len(val)>(8000 if key=='purpose' else 4000 if key=='notes' else 200):raise ValueError('Invalid registry field')
        if (create or 'name' in data) and not data.get('name','').strip():raise ValueError('Agent name required')
        if 'lifecycle' in data and data['lifecycle'] not in LIFECYCLES:raise ValueError('Invalid lifecycle')
        if 'review' in data and data['review'] not in ('unreviewed','reviewed'):raise ValueError('Invalid review state')
        return {k:v.strip() for k,v in data.items()}

    def _create(self,data,manual=True):
        data=self._validate_entity(data,True);eid='agt_'+uuid.uuid4().hex[:20];stamp=now()
        self.db.execute('INSERT INTO catproof_entities(id,name,purpose,business_owner,lifecycle,review,notes,created_at,updated_at,reviewed_at,manual) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                        (eid,data['name'],data.get('purpose',''),data.get('business_owner',''),data.get('lifecycle','draft'),data.get('review','unreviewed'),data.get('notes',''),stamp,stamp,stamp if data.get('review')=='reviewed' else None,int(manual)))
        return eid

    def create(self,data):
        with self.lock,self.db:
            eid=self._create(data);self.event('registered',data['name'],{'method':'manual registration'},eid)
        return eid

    def update(self,eid,data):
        data=self._validate_entity(data)
        with self.lock,self.db:
            entity=self.db.execute('SELECT * FROM catproof_entities WHERE id=? AND archived=0',(eid,)).fetchone()
            if not entity:raise KeyError('Canonical agent not found')
            changes={k:{'before':entity[k],'after':v} for k,v in data.items() if entity[k]!=v}
            for k,v in data.items():self.db.execute('UPDATE catproof_entities SET '+k+'=?,updated_at=? WHERE id=?',(v,now(),eid))
            if 'review' in changes:self.db.execute('UPDATE catproof_entities SET reviewed_at=? WHERE id=?',(now() if data['review']=='reviewed' else None,eid))
            if changes:self.event('registry_updated',data.get('name',entity['name']),changes,eid)
        return self.snapshot()

    def reconcile(self,data):
        """Apply an explicit decision only against the exact record/evidence reviewed."""
        if not isinstance(data,dict):raise ValueError('Invalid reconciliation decision')
        with self.lock,self.db:
            snapshot=self.snapshot()
            agent=next((a for a in snapshot['agents'] if a['registry_id']==data.get('registry_id')),None)
            if not agent:raise KeyError('Canonical agent not found')
            if data.get('expected_updated_at')!=agent['updated_at'] or data.get('expected_record')!={k:agent[k] for k in ('name','purpose','business_owner','lifecycle','review','notes')}:raise ValueError('Record changed. Refresh and review it again.')
            decision=data.get('decision')
            if decision=='approve':
                if not agent['business_owner'].strip() or not agent['purpose'].strip():raise ValueError('Add an owner and purpose before approving this record.')
                updates={'review':'reviewed'}
            elif decision=='accept':
                field=data.get('field');source_field={'name':'name','business_owner':'owner'}.get(field)
                if not source_field:raise ValueError('Only name and owner can be accepted from a source')
                observation=next((o for o in agent['linked_records'] if o['observation_id']==data.get('observation_id')),None)
                if not observation:raise ValueError('Source link changed. Refresh and review it again.')
                source=next(s for s in snapshot['sources'] if s['id']==observation['connection_id'])
                if observation['stale'] or not observation['present'] or not source['enabled'] or source['health']!='ok':raise ValueError('A successful current source read is required')
                value=observation.get(source_field,'')
                if not value.strip() or (source_field=='owner' and value.strip().lower() in ('unassigned','unknown','none','n/a','null','not set')) or value!=data.get('expected_observed'):raise ValueError('Source value changed. Refresh and review it again.')
                updates={field:value}
            else:raise ValueError('Unsupported reconciliation decision')
            self.update(agent['registry_id'],updates)
            self.event('reconciled',agent['name'],{'decision':decision,'field':data.get('field'),'observation_id':data.get('observation_id')},agent['registry_id'])
        return self.snapshot()

    def import_agents(self, rows):
        if not isinstance(rows,list) or not 1 <= len(rows) <= 100:
            raise ValueError('Import between 1 and 100 agents at a time')
        validated=[self._validate_entity(row,True) for row in rows]
        ids=[]
        with self.lock,self.db:
            for row in validated:
                eid=self._create(row);ids.append(eid)
                self.event('registered',row['name'],{'method':'CSV import'},eid)
        return dict(self.snapshot(),imported_ids=ids)

    def assign_owner(self, ids, owner):
        if not isinstance(ids,list) or not 1 <= len(ids) <= 100 or any(not isinstance(i,str) for i in ids) or len(set(ids))!=len(ids):
            raise ValueError('Select between 1 and 100 unique agents')
        owner=self._validate_entity({'business_owner':owner})['business_owner']
        if not owner:raise ValueError('Enter an owner or team')
        with self.lock,self.db:
            entities=[self.db.execute('SELECT * FROM catproof_entities WHERE id=? AND archived=0',(eid,)).fetchone() for eid in ids]
            if any(e is None for e in entities):raise KeyError('Selected agent no longer exists')
            for e in entities:
                if e['business_owner']==owner:continue
                self.db.execute('UPDATE catproof_entities SET business_owner=?,updated_at=? WHERE id=?',(owner,now(),e['id']))
                self.event('registry_updated',e['name'],{'business_owner':{'before':e['business_owner'],'after':owner}},e['id'])
        return self.snapshot()

    def link(self,observation_id,target_id,expected_current=None):
        """Explicitly reassign ONE source identity; no name-based merging or metadata overwrites."""
        with self.lock,self.db:
            target=self.db.execute('SELECT * FROM catproof_entities WHERE id=? AND archived=0',(target_id,)).fetchone()
            binding=self.db.execute('SELECT b.*,o.payload FROM catproof_bindings b JOIN catproof_observations o ON o.id=b.observation_id WHERE b.observation_id=?',(observation_id,)).fetchone()
            if not target or not binding:raise KeyError('Agent or observation not found')
            old_id=binding['entity_id']
            if expected_current is not None and old_id!=expected_current:
                raise ValueError('This source record has moved since you opened it. Refresh and review the current link.')
            if old_id==target_id:return self.snapshot()
            payload=json.loads(binding['payload'])
            old_seed=self.db.execute('SELECT o.payload FROM catproof_observations o JOIN catproof_bindings b ON b.observation_id=o.id WHERE b.entity_id=?',(target_id,)).fetchall()
            target_seed={json.loads(r['payload'])['provenance']=='seeded' for r in old_seed}
            if (target_seed and (payload['provenance']=='seeded') not in target_seed) or (not target_seed and target['manual'] in (0,2) and (payload['provenance']=='seeded') != (target['manual']==2)):raise ValueError('Seeded and real source records cannot be linked together')
            self.db.execute('UPDATE catproof_bindings SET entity_id=? WHERE observation_id=?',(target_id,observation_id))
            self.event('source_unlinked',self.db.execute('SELECT name FROM catproof_entities WHERE id=?',(old_id,)).fetchone()['name'],{'observation_id':observation_id,'moved_to':target_id},old_id)
            self.event('source_linked',target['name'],{'observation_id':observation_id,'moved_from':old_id},target_id)
            # Preserve the original canonical record and its decisions. It remains available as an unlinked record.
        return self.snapshot()

    def unlink(self,oid):
        with self.lock,self.db:
            row=self.db.execute('SELECT o.payload,b.entity_id FROM catproof_observations o JOIN catproof_bindings b ON b.observation_id=o.id WHERE o.id=?',(oid,)).fetchone()
            if not row:raise KeyError('Observation not found')
            payload=json.loads(row['payload']);eid=self._create({'name':payload['name'][:200],'purpose':payload.get('description','')[:8000]},manual=2 if payload['provenance']=='seeded' else 0)
            self.db.execute('UPDATE catproof_bindings SET entity_id=? WHERE observation_id=?',(eid,oid))
            self.event('source_unlinked',payload['name'],{'observation_id':oid,'new_canonical_id':eid},row['entity_id'])
            self.event('source_linked',payload['name'],{'observation_id':oid,'split_from':row['entity_id']},eid)
        return self.snapshot()

    def snapshot(self):
        with self.lock:
            conns=[dict(c) for c in self.db.execute('SELECT * FROM catproof_connections ORDER BY created_at,id')]
            configured=self.configured_connection_ids();available=set(self.available_connection_ids())
            for c in conns:
                c['within_limit']=c['id'] in available
                c['configured_enabled']=bool(c['enabled'])
                c['enabled']=bool(c['enabled']) and c['within_limit']
            for c in conns:c['config']=json.loads(c['config'])
            cmap={c['id']:c for c in conns};observations=[]
            assets=[];asset_links={}
            for link in self.db.execute('SELECT asset_id,entity_id FROM catproof_asset_links'):
                asset_links.setdefault(link['asset_id'],[]).append(link['entity_id'])
            for saved in self.db.execute('SELECT * FROM catproof_assets'):
                a=json.loads(saved['payload']);a.update(asset_id=saved['id'],connection_id=saved['connection_id'],connection_name=cmap[saved['connection_id']]['name'],connection_enabled=bool(cmap[saved['connection_id']]['enabled']),present=bool(saved['present']),stale=bool(saved['stale']) or not cmap[saved['connection_id']]['within_limit'],linked_agents=asset_links.get(saved['id'],[]));assets.append(a)
            for o in self.db.execute('SELECT o.*,b.entity_id FROM catproof_observations o JOIN catproof_bindings b ON b.observation_id=o.id'):
                row=json.loads(o['payload']);row.update(observation_id=o['id'],registry_id=o['entity_id'],connection_id=o['connection_id'],connection_name=cmap[o['connection_id']]['name'],connection_enabled=bool(cmap[o['connection_id']]['enabled']),first_seen=o['first_seen'],present=bool(o['present']),stale=bool(o['stale']) or not cmap[o['connection_id']]['within_limit'])
                observations.append(row)
            agents=[]
            for e in self.db.execute('SELECT * FROM catproof_entities WHERE archived=0 ORDER BY created_at,id'):
                links=[o for o in observations if o['registry_id']==e['id']];values={o['status'] for o in links if o['present']};all_seed=bool(links) and all(o['provenance']=='seeded' for o in links)
                attention=[]
                if not e['business_owner']:attention.append('Business owner missing')
                if e['review']=='unreviewed':attention.append('Not reviewed')
                if not links:attention.append('No linked source identities')
                if any(o['stale'] for o in links):attention.append('Source refresh failed or paused')
                if links and not any(o['present'] for o in links):attention.append('No longer observed')
                if len(values)>1:attention.append('Conflicting source statuses')
                status=next(iter(values)) if len(values)==1 else 'conflict' if values else 'unknown'
                row=dict(e);row.update(registry_id=e['id'],description=e['purpose'],kind='Canonical agent',source=', '.join(sorted({o['source'] for o in links})) or 'Registered locally',
                    source_id=e['id'],owner=', '.join(sorted({o['owner'] for o in links})) or 'Unassigned',source_status=status,status=status,
                    provenance='seeded' if all_seed or (not links and e['manual']==2) else 'live' if links or e['manual']==0 else 'manual',linked_records=links,source_count=len({o['connection_id'] for o in links}),
                    last_seen=max((o['last_seen'] for o in links),default=None),first_seen=e['created_at'],present=any(o['present'] for o in links) if links else True,
                    stale=any(o['stale'] for o in links),attention=attention)
                row['linked_assets']=[a for a in assets if e['id'] in a['linked_agents']]
                row['source_enabled']=any(cmap[o['connection_id']]['enabled'] for o in links) if links else True
                agents.append(row)
            events=[dict(e) for e in self.db.execute('SELECT * FROM catproof_registry_events ORDER BY sequence DESC LIMIT 500')]
            for e in events:e['detail']=json.loads(e['detail']);e['registry_id']=e['entity_id']
            sources=[]
            for c in conns:
                records=[o for o in observations if o['connection_id']==c['id']]
                sources.append(dict(c,credential_storage='encrypted' if getattr(self,'credentials',{}).get(c['id']) else 'operator' if self.has_credentials(c) else 'none',credential_configured=self.has_credentials(c),auth_optional=c['kind'] in OPTIONAL,health='archived' if c['archived'] else 'limited' if not c['within_limit'] else 'paused' if not c['enabled'] else 'error' if c['error'] else 'ok' if c['last_success'] else 'pending',record_count=sum(o['present'] for o in records),asset_count=sum(a['present'] for a in assets if a['connection_id']==c['id']),coverage=CATALOG.get(c['kind'],{}).get('coverage','Configured source inventory'),verification=CATALOG.get(c['kind'],{}).get('verification','Previously available connector')))
            runs=[dict(r) for r in self.db.execute('SELECT * FROM catproof_sync_runs ORDER BY completed_at DESC,sequence DESC LIMIT 100')]
            return {'agents':agents,'observations':observations,'assets':assets,'sources':sources,'events':events,'runs':runs,'namespace':'Federated registry','connection_limits':{'edition':'Community Edition','maximum':self.connection_limit,'used':len(configured),'available':None if self.connection_limit is None else max(0,self.connection_limit-len(configured)),'over_limit':self.connection_limit is not None and len(configured)>self.connection_limit},
                    'synced_at':max((c['last_success'] for c in conns if c['last_success']),default=None)}

    def link_asset(self,asset_id,entity_id,linked=True):
        if not isinstance(linked,bool):raise ValueError('Linked must be a boolean')
        with self.lock,self.db:
            a=self.db.execute('SELECT * FROM catproof_assets WHERE id=?',(asset_id,)).fetchone()
            e=self.db.execute('SELECT * FROM catproof_entities WHERE id=?',(entity_id,)).fetchone()
            if not a or not e:raise KeyError('Asset or canonical agent not found')
            payload=json.loads(a['payload'])
            linked_rows=[json.loads(r['payload']) for r in self.db.execute('SELECT o.payload FROM catproof_observations o JOIN catproof_bindings b ON b.observation_id=o.id WHERE b.entity_id=?',(entity_id,))]
            if linked and linked_rows and any((r['provenance']=='seeded')!=(payload['provenance']=='seeded') for r in linked_rows):raise ValueError('Seeded and real evidence cannot be linked together')
            if linked:self.db.execute('INSERT INTO catproof_asset_links(asset_id,entity_id) VALUES(?,?) ON CONFLICT DO NOTHING',(asset_id,entity_id))
            else:self.db.execute('DELETE FROM catproof_asset_links WHERE asset_id=? AND entity_id=?',(asset_id,entity_id))
            self.event('asset_linked' if linked else 'asset_unlinked',e['name'],{'asset_id':asset_id,'source':json.loads(a['payload'])['source']},entity_id)
        return self.snapshot()

    def import_legacy(self,path,namespace):
        """One-time, local-only import of prior accountability fields; leaves old DB intact."""
        from pathlib import Path
        path=Path(path).resolve()
        key=hashlib.sha256((str(path)+'\0'+namespace).encode()).hexdigest()
        with self.lock:
            if self.db.execute('SELECT id FROM catproof_migrations WHERE id=?',(key,)).fetchone():return 0
        import sqlite3  # Read-only compatibility import; never used for runtime persistence.
        old=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True);old.row_factory=sqlite3.Row
        try:records=old.execute('SELECT * FROM agents WHERE namespace=?',(namespace,)).fetchall()
        finally:old.close()
        count=0
        with self.lock,self.db:
            observations=self.db.execute('SELECT o.payload,b.entity_id FROM catproof_observations o JOIN catproof_bindings b ON b.observation_id=o.id').fetchall()
            for record in records:
                if not (record['business_owner'] or record['notes'] or record['review']!='unreviewed'):continue
                payload=json.loads(record['payload'])
                matches=[o for o in observations if json.loads(o['payload']).get('id')==payload['id'] and json.loads(o['payload']).get('provenance')==payload['provenance']]
                if len(matches)!=1:continue
                eid=matches[0]['entity_id']
                review='reviewed' if record['review']=='reviewed' else 'unreviewed'
                lifecycle='retired' if record['review']=='retired' else 'draft'
                self.db.execute('UPDATE catproof_entities SET business_owner=?,notes=?,review=?,reviewed_at=?,lifecycle=?,updated_at=? WHERE id=?',
                                (record['business_owner'],record['notes'],review,record['reviewed_at'],lifecycle,now(),eid))
                self.event('legacy_annotations_imported',payload['name'],{'from':'Previous local registry','fields':'business owner, review, notes'},eid)
                count+=1
            self.db.execute('INSERT INTO catproof_migrations(id,applied_at) VALUES(?,?)',(key,now()))
        return count

    def catalog(self,include_seeded=False):
        # Portable single-hop registry contract. Canonical lifecycle is not substituted for source runtime status.
        rows=[]
        snapshot=self.snapshot()
        connection_kinds={c['id']:c['kind'] for c in snapshot['sources']}
        for a in snapshot['agents']:
            if not a['source_enabled']:continue
            # Imported peer-only records are not re-exported: avoid recursive federation loops.
            if a['manual']==0 and all(connection_kinds[o['connection_id']]=='atlas' for o in a['linked_records']):
                continue
            if a['provenance']=='seeded' and not include_seeded:continue
            rows.append({'id':a['registry_id'],'source_id':a['registry_id'],'name':a['name'],'description':a['description'],
                         'owner':a['business_owner'] or 'Unassigned','source':'CatProof','kind':'Canonical agent',
                         'status':a['status'],'source_status':a['status'],'last_seen':a['last_seen'] or a['updated_at'],'provenance':a['provenance'],
                         'lifecycle':a['lifecycle'],'observed_at':a['last_seen'],'catalog_read_at':now(),
                         'stale':a['stale'],'present':a['present']})
        degraded=any(a['stale'] or not a['present'] for a in rows)
        return {'schema_version':'atlas.registry.v1','evidence_version':1,'agents':rows,'sources':[{'name':'CatProof','health':'degraded' if degraded else 'ok','mode':'live'}]}

    def export(self,include_seeded=False):
        out=io.StringIO();fields=['registry_id','name','business_owner','lifecycle','review','source','source_count','status','provenance','last_seen','notes']
        writer=csv.writer(out);writer.writerow(fields)
        for a in self.snapshot()['agents']:
            if not a['source_enabled']:continue
            if a['provenance']=='seeded' and not include_seeded:continue
            row=[]
            for k in fields:
                value=str(a.get(k) or '')
                if value.lstrip().startswith(('=','+','-','@')) or value.startswith(('\t','\n','\r')):value="'"+value
                row.append(value)
            writer.writerow(row)
        return out.getvalue().encode('utf-8-sig')
