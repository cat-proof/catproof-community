import os
"""Persistent local product; can reuse a running credentialed discovery server."""
import argparse
import json
from pathlib import Path
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.parse import urlsplit
from app import Handler as BaseHandler
from registry import Registry
from product import ProductStore

STATIC=Path(__file__).parent/'product_static'

class ProductHandler(BaseHandler):
    def do_GET(self):
        if not self.allowed(): return self.reply(403,{'error':'Local host required'})
        if self.path=='/api/inventory': return self.reply(200,self.server.store.snapshot())
        if self.path in ('/api/export.csv','/api/export.csv?seeded=1'): return self.reply(200,self.server.store.export(self.path.endswith('?seeded=1')),'text/csv; charset=utf-8')
        files={'/':('index.html','text/html; charset=utf-8'),'/app.js':('app.js','text/javascript'),'/style.css':('style.css','text/css')}
        if self.path not in files:return self.reply(404,{'error':'Not found'})
        name,kind=files[self.path]; self.reply(200,(STATIC/name).read_bytes(),kind)

    def do_POST(self):
        origin=self.headers.get('Origin')
        valid={'http://127.0.0.1:'+str(self.server.server_port),'http://localhost:'+str(self.server.server_port)}
        if not self.allowed() or (origin and origin not in valid) or self.headers.get('X-Registry-Action')!='refresh':
            return self.reply(403,{'error':'Same-origin registry action required'})
        try:
            if self.path=='/api/refresh':
                return self.reply(200,self.server.store.ingest(self.server.discover(True)))
            if self.path=='/api/annotate':
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=20000:raise ValueError('Invalid request size')
                body=json.loads(self.rfile.read(size))
                if not isinstance(body,dict):raise ValueError('Expected an object')
                return self.reply(200,self.server.store.annotate(body['registry_id'],body['updates']))
            return self.reply(404,{'error':'Not found'})
        except KeyError:return self.reply(404,{'error':'Agent or required field not found'})
        except (ValueError,TypeError):return self.reply(400,{'error':'Invalid registry update or inventory response'})
        except Exception:return self.reply(502,{'error':'Discovery unavailable. Saved inventory retained; retry when the connector is available.'})

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--upstream',help='Existing local discovery server, e.g. http://127.0.0.1:8766')
    parser.add_argument('--namespace',required=True,help='Stable workspace/instance label; separates stored inventories')
    parser.add_argument('--port',type=int,default=8770)
    parser.add_argument('--database-url',default=os.getenv('DATABASE_URL'),help='Migrated PostgreSQL database URL')
    args=parser.parse_args()
    if not args.database_url:parser.error('Set DATABASE_URL or --database-url; migrate first')
    store=ProductStore(args.database_url,args.namespace)
    if args.upstream:
        parsed=urlsplit(args.upstream)
        if parsed.scheme!='http' or parsed.hostname not in ('127.0.0.1','localhost') or not parsed.port or parsed.username or parsed.password or parsed.path not in ('','/') or parsed.query or parsed.fragment or parsed.port==args.port:
            parser.error('Upstream must be another localhost HTTP origin')
        base=args.upstream.rstrip('/')
        def discover(refresh):
            req=Request(base+('/api/refresh' if refresh else '/api/inventory'),method='POST' if refresh else 'GET',headers={'X-Registry-Action':'refresh'})
            with urlopen(req,timeout=180) as response:
                payload=response.read(20_000_001)
                if len(payload)>20_000_000:raise ValueError('Discovery payload too large')
                return json.loads(payload)
    else:
        registry=Registry()
        def discover(refresh):return registry.refresh()
    try:store.ingest(discover(False))
    except Exception:print('Discovery unavailable; showing saved inventory.',flush=True)
    server=ThreadingHTTPServer(('127.0.0.1',args.port),ProductHandler)
    server.store=store;server.discover=discover
    print('Agent Atlas product: http://127.0.0.1:'+str(args.port),flush=True)
    server.serve_forever()

if __name__=='__main__':main()
