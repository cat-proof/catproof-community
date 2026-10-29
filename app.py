"""Local-only HTTP dashboard. Run: python3 app.py"""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from registry import Registry

STATIC = Path(__file__).parent / 'static'
registry = Registry()

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, code, payload, kind='application/json'):
        body = json.dumps(payload).encode() if kind == 'application/json' else payload
        self.send_response(code)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'")
        self.end_headers()
        if self.command!='HEAD':self.wfile.write(body)

    def allowed(self):
        expected = {'127.0.0.1:'+str(self.server.server_port), 'localhost:'+str(self.server.server_port)}
        return self.headers.get('Host') in expected

    def do_GET(self):
        if not self.allowed():
            return self.reply(403, {'error':'Local host required'})
        if self.path == '/api/inventory':
            return self.reply(200, registry.snapshot())
        files = {'/':('index.html','text/html; charset=utf-8'), '/app.js':('app.js','text/javascript'), '/style.css':('style.css','text/css')}
        if self.path not in files:
            return self.reply(404, {'error':'Not found'})
        name, kind = files[self.path]
        self.reply(200, (STATIC/name).read_bytes(), kind)

    def do_POST(self):
        origin = self.headers.get('Origin')
        allowed_origins = {'http://127.0.0.1:'+str(self.server.server_port), 'http://localhost:'+str(self.server.server_port)}
        if not self.allowed() or (origin and origin not in allowed_origins) or self.headers.get('X-Registry-Action') != 'refresh':
            return self.reply(403, {'error':'Same-origin registry action required'})
        if self.path != '/api/refresh':
            return self.reply(404, {'error':'Not found'})
        self.reply(200, registry.refresh())

if __name__ == '__main__':
    port = int(os.environ.get('PORT','8765'))
    registry.refresh()
    print('Agent Registry running at http://127.0.0.1:%s' % port, flush=True)
    ThreadingHTTPServer(('127.0.0.1',port),Handler).serve_forever()
