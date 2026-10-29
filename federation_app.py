"""Local federated registry service: canonical records + named registry connections."""
import argparse
import json
import os
from pathlib import Path
from http.server import ThreadingHTTPServer
from product_app import ProductHandler
from federation import FederatedRegistry,DuplicateConnection
from adapters import CATALOG
from adapters.catalog import BASE_CATALOG
from marketing import ASSETS,asset,discovery_file
from adapters.setup import check_setup, TOP_FIVE, GUIDE_FAMILIES

STATIC=Path(__file__).parent/'federation_static'
DEMO_STATIC=Path(__file__).parent/'demo_static'
DEMO_FILES={'/demo':('index.html','text/html; charset=utf-8'),'/demo/':('index.html','text/html; charset=utf-8'),'/demo/style.css':('style.css','text/css'),'/demo/model.js':('model.js','text/javascript'),'/demo/app.js':('app.js','text/javascript')}

def demo_file(path):
    if path not in DEMO_FILES:return None
    name,kind=DEMO_FILES[path]
    return (DEMO_STATIC/name).read_bytes(),kind

class FederationHandler(ProductHandler):
    @property
    def registry(self):
        return getattr(self, 'request_store', None) or self.server.store

    def action_origins(self):
        return {'http://127.0.0.1:'+str(self.server.server_port),'http://localhost:'+str(self.server.server_port)}

    def do_GET(self):
        if not self.allowed():return self.reply(403,{'error':'Local host required'})
        if self.path in ASSETS:
            body,kind=asset(self.path);return self.reply(200,body,kind)
        if self.path=='/site.webmanifest':
            body,kind=discovery_file(self.path);return self.reply(200,body,kind)
        if self.path in DEMO_FILES:
            body,kind=demo_file(self.path);return self.reply(200,body,kind)
        if self.path=='/api/contract':return self.reply(200,{'schema_version':'atlas.registry.v1','catalog':'GET /api/catalog','discovery':'GET /api/inventory','required_agent_fields':['id','name','source','owner','status','provenance','last_seen'],'provenance':['live','seeded','manual'],'evidence_version':1,'freshness_fields':['stale','present','observed_at','catalog_read_at'],'source_health':'ok or degraded with evidence_version=1 and explicit per-record freshness; otherwise errors retain the previous snapshot','operations':'Discovery only; canonical ownership and lifecycle stay local'})
        if self.path=='/api/setup-guides':return self.reply(200,{'guides':{key:(Path(__file__).parent/'guides'/(key+'.md')).read_text() for key in ('snowflake','copilot','aws','a2a','google','lovable','credentials')},'families':GUIDE_FAMILIES})
        if self.path=='/api/connector-catalog':return self.reply(200,{'connectors':{**CATALOG,**BASE_CATALOG},'top_five':TOP_FIVE})
        if self.path=='/api/assets':return self.reply(200,{'assets':self.registry.snapshot()['assets']})
        if self.path=='/api/inventory':return self.reply(200,self.registry.snapshot())
        if self.path in ('/api/connections','/api/observations','/api/history'):
            key={'/api/connections':'sources','/api/observations':'observations','/api/history':'events'}[self.path]
            return self.reply(200,{key:self.registry.snapshot()[key]})
        if self.path.startswith('/api/agents/'):
            agent=next((a for a in self.registry.snapshot()['agents'] if a['registry_id']==self.path.split('/')[-1]),None)
            return self.reply(200,agent) if agent else self.reply(404,{'error':'Canonical agent not found'})
        if self.path=='/api/catalog':return self.reply(200,self.registry.catalog())
        if self.path in ('/api/export.csv','/api/export.csv?seeded=1'):
            return self.reply(200,self.registry.export(self.path.endswith('?seeded=1')),'text/csv; charset=utf-8')
        files={'/':('index.html','text/html; charset=utf-8'),'/app.js':('app.js','text/javascript'),'/workflow-ui.js':('workflow-ui.js','text/javascript'),'/security.js':('security.js','text/javascript'),'/local.js':('local.js','text/javascript'),'/graph-model.js':('graph-model.js','text/javascript'),'/graph-ui.js':('graph-ui.js','text/javascript'),'/mapping-model.js':('mapping-model.js','text/javascript'),'/mapping-ui.js':('mapping-ui.js','text/javascript'),'/style.css':('style.css','text/css')}
        if self.path not in files:return self.reply(404,{'error':'Not found'})
        name,kind=files[self.path];return self.reply(200,(STATIC/name).read_bytes(),kind)

    def do_POST(self):
        valid=self.action_origins()
        origin=self.headers.get('Origin')
        if not self.allowed() or (origin and origin not in valid) or self.headers.get('X-Registry-Action')!='refresh':
            return self.reply(403,{'error':'Same-origin registry action required'})
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=(250000 if self.path=='/api/agents/import' else 25000):raise ValueError('Invalid request size')
            data=json.loads(self.rfile.read(length))
            if not isinstance(data,dict):raise ValueError('Expected an object')
            store=self.registry
            if self.path=='/api/connection-check':
                connection=next((c for c in store.snapshot()['sources'] if c['id']==data['connection_id']),None)
                if not connection:raise KeyError('Connection not found')
                return self.reply(200,check_setup(connection,store.credential_env(connection)))
            if self.path=='/api/refresh':return self.reply(200,store.refresh(data.get('connection_id')))
            if self.path=='/api/agents':
                eid=store.create(data);return self.reply(201,dict(store.snapshot(),created_id=eid))
            if self.path=='/api/agents/import':return self.reply(201,store.import_agents(data.get('rows')))
            if self.path=='/api/agents/assign-owner':return self.reply(200,store.assign_owner(data.get('ids'),data.get('owner')))
            if self.path=='/api/reconcile':return self.reply(200,store.reconcile(data))
            if self.path=='/api/annotate':return self.reply(200,store.update(data['registry_id'],data['updates']))
            if self.path=='/api/asset-link':return self.reply(200,store.link_asset(data['asset_id'],data['registry_id'],data.get('linked',True)))
            if self.path=='/api/link':return self.reply(200,store.link(data['observation_id'],data['registry_id'],data.get('expected_current')))
            if self.path=='/api/unlink':return self.reply(200,store.unlink(data['observation_id']))
            if self.path=='/api/connections':
                cid=store.add_connection(data);return self.reply(201,dict(store.snapshot(),created_connection_id=cid))
            if self.path=='/api/connection-state':
                store.set_enabled(data['connection_id'],data['enabled']);return self.reply(200,store.snapshot())
            return self.reply(404,{'error':'Not found'})
        except DuplicateConnection as exc:return self.reply(409,{'error':str(exc),'connection_id':exc.connection_id})
        except KeyError:return self.reply(404,{'error':'Agent, connection or required field not found'})
        except (ValueError,TypeError) as exc:return self.reply(400,{'error':str(exc) if isinstance(exc,ValueError) and not isinstance(exc,json.JSONDecodeError) else 'Invalid request'})
        except Exception:return self.reply(500,{'error':'Registry operation failed; retry or inspect local configuration.'})

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=8770)
    parser.add_argument('--database-url',default=os.getenv('DATABASE_URL'),help='Migrated PostgreSQL database URL')
    parser.add_argument('--upstream',help='Bootstrap an existing local Databricks discovery bridge')
    parser.add_argument('--legacy-db',help='Optional previous local registry database to import accountability fields')
    parser.add_argument('--legacy-namespace',default='')
    parser.add_argument('--from-env',action='store_true',help='Register native connections from existing server environment credentials')
    parser.add_argument('--empty',action='store_true',help='Start without bootstrap demo connections')
    args=parser.parse_args()
    if not args.database_url:parser.error('Set DATABASE_URL or --database-url; run python database.py upgrade first')
    store=FederatedRegistry(args.database_url,local_port=args.port)
    if args.from_env:
        for kind,host_key,prefix,name in [('databricks','DATABRICKS_HOST','DATABRICKS','Databricks · workspace'),('servicenow','SERVICENOW_INSTANCE_URL','SERVICENOW','ServiceNow · instance')]:
            host=os.environ.get(host_key)
            if host and not any(c['kind']==kind and c['url']==host.rstrip('/') for c in store.snapshot()['sources']):
                store.add_connection({'name':name,'kind':kind,'url':host,'credential_prefix':prefix})
    if not store.snapshot()['sources'] and not args.empty:
        if args.upstream:
            store.add_connection({'name':'Databricks · primary workspace','kind':'discovery','url':args.upstream,'source_filter':'Databricks'})
        store.add_connection({'name':'ServiceNow · demo (VPN deferred)','kind':'seeded'})
        store.refresh(force=False)
    if args.from_env:
        store.refresh(force=False)
    if args.legacy_db:
        print('Imported prior local decisions: '+str(store.import_legacy(args.legacy_db,args.legacy_namespace)),flush=True)
    server=ThreadingHTTPServer(('127.0.0.1',args.port),FederationHandler);server.store=store
    print('CatProof federated registry: http://127.0.0.1:'+str(args.port),flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close();store.db.close()

if __name__=='__main__':main()
