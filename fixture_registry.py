"""Independent, explicitly fictional registry for demonstrating federation over HTTP."""
import argparse
import json
from http.server import ThreadingHTTPServer
from registry import now
from app import Handler

class FixtureHandler(Handler):
    def do_GET(self):
        if not self.allowed():return self.reply(403,{'error':'Local host required'})
        if self.path=='/.well-known/agent-card.json':
            return self.reply(200,{'name':'Demo · A2A policy assistant','description':'Fictional Agent Card for local connector verification.','version':'1.0','skills':[{'id':'policy','name':'Policy lookup','description':'Advertised demo capability'}],'supportedInterfaces':[{'url':'http://127.0.0.1:'+str(self.server.server_port)+'/a2a','protocolVersion':'1.0','protocolBinding':'JSONRPC'}],'x-catproof-demo':True})
        if self.path.startswith('/v0.1/servers'):
            return self.reply(200,{'servers':[{'server':{'name':'ai.catproof.demo/policy-tools','title':'Demo policy tools','version':'1.0.0','description':'Fictional tool server; not installed or executed.'},'_meta':{'ai.catproof/demo':True}}],'metadata':{}})
        if self.path!='/api/catalog':return self.reply(404,{'error':'Not found'})
        stamp=now()
        rows=[dict(id='fixture-internal-'+str(i),source_id='fixture-internal-'+str(i),name=name,
                   source='Internal registry (seeded)',kind='Internal Agent',owner='Demo · Enterprise AI',
                   status='active',source_status='SIMULATED',description='Fictional record in an independent demo registry.',
                   provenance='seeded',last_seen=stamp) for i,name in enumerate(['Incident triage','Policy routing'])]
        return self.reply(200,{'schema_version':'atlas.registry.v1','agents':rows,'sources':[{'name':'Internal registry (seeded)','mode':'seeded','health':'ok'}]})
    def do_POST(self):return self.reply(405,{'error':'Read-only fixture registry'})

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8772);args=parser.parse_args()
    server=ThreadingHTTPServer(('127.0.0.1',args.port),FixtureHandler)
    print('Seeded fixture registry: http://127.0.0.1:'+str(args.port),flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()
