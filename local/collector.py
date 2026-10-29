#!/usr/bin/env python3
"""CatProof Local v1: explicit metadata discovery and outbound HTTPS only. Python 3.11+."""
import argparse
import contextlib
import fcntl
import getpass
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request,build_opener,HTTPRedirectHandler,HTTPSHandler,ProxyHandler

VERSION='1.0.2'
EVENTS={'SessionStart','SessionEnd','UserPromptSubmit','Stop'}
class LocalError(Exception):pass
class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

def origin(value):
    p=urlsplit(value)
    if p.scheme!='https' or not p.hostname or p.username or p.password or p.path not in ('','/') or p.query or p.fragment or (p.port not in (None,443) and p.hostname not in ('localhost','127.0.0.1','::1')):raise LocalError('Use an HTTPS CatProof application origin, without a path or credentials')
    return value.rstrip('/')

def safe_json(path,default=None):
    if not path.exists():return {} if default is None else default
    if path.is_symlink() or not path.is_file() or path.stat().st_size>1_000_000:raise LocalError('Unsafe or oversized configuration file')
    try:return json.loads(path.read_text())
    except (ValueError,UnicodeError):raise LocalError('Invalid JSON configuration; no changes made') from None

def private_dir(path):
    if path.is_symlink():raise LocalError('State directory cannot be a symlink')
    path.mkdir(parents=True,exist_ok=True,mode=0o700)
    if path.stat().st_uid!=os.getuid() or path.stat().st_mode&0o077:raise LocalError('State directory must be owned by you with permissions 700')

@contextlib.contextmanager
def locked(state,name):
    private_dir(state);p=state/(name+'.lock')
    fd=os.open(p,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX);yield
    finally:os.close(fd)

def save(path,data):
    private_dir(path.parent)
    fd,temp=tempfile.mkstemp(dir=path.parent,prefix='.catproof-')
    try:
        with os.fdopen(fd,'w') as f:json.dump(data,f);f.flush();os.fsync(f.fileno())
        os.replace(temp,path)
    finally:
        if os.path.exists(temp):os.unlink(temp)

def config(state):
    path=state/'connection.json'
    if not path.exists():raise LocalError('Enroll this device first')
    if path.is_symlink() or path.stat().st_uid!=os.getuid() or path.stat().st_mode&0o077:raise LocalError('Connection file must be private (permissions 600)')
    return safe_json(path)

def names(values):
    return sorted({x for x in values if isinstance(x,str) and re.fullmatch(r'[A-Za-z0-9 _./:-]{1,100}',x)})[:50]

def discover(home=None,include_config=False,which=shutil.which,runner=subprocess.run):
    home=Path.home() if home is None else Path(home);tools=[]
    for tool in ('codex','claude'):
        binary=which(tool);installed=bool(binary)
        if tool=='codex' and (home/'Applications/Codex.app').is_dir() or tool=='codex' and Path('/Applications/Codex.app').is_dir():installed=True
        version=''
        if binary:
            try:
                r=runner([binary,'--version'],capture_output=True,text=True,timeout=3,check=False)
                match=re.search(r'\b(\d+\.\d+(?:\.\d+)?(?:[-+][0-9A-Za-z.-]+)?)\b',r.stdout[:200])
                if match:version=match.group(1)[:40]
            except (OSError,subprocess.SubprocessError):pass
        agents=[];mcp=[]
        if include_config:
            base=home/('.codex' if tool=='codex' else '.claude')
            agent_dir=base/'agents'
            if agent_dir.is_dir() and not agent_dir.is_symlink():agents=[p.stem for p in agent_dir.glob('*.toml' if tool=='codex' else '*.md') if not p.is_symlink() and p.is_file()]
            if tool=='codex':
                import tomllib
                file=base/'config.toml'
                if file.exists():
                    if file.is_symlink() or file.stat().st_size>1_000_000:raise LocalError('Unsafe or oversized Codex configuration')
                    try:data=tomllib.loads(file.read_text())
                    except (ValueError,UnicodeError):raise LocalError('Cannot parse Codex config; no report sent') from None
                    agents+=list(data.get('agents',{})) if isinstance(data.get('agents'),dict) else []
                    mcp=list(data.get('mcp_servers',{})) if isinstance(data.get('mcp_servers'),dict) else []
                    agents=[x for x in agents if x not in ('max_threads','max_depth','job_max_runtime_seconds')]
            else:
                data=safe_json(home/'.claude.json');mcp=list(data.get('mcpServers',{})) if isinstance(data,dict) and isinstance(data.get('mcpServers'),dict) else []
        tools.append({'tool':tool,'installed':installed,'version':version,'agents':names(agents),'mcp_servers':names(mcp)})
    return tools

def activity(state,now=None):
    now=time.time() if now is None else now
    with locked(state,'activity'):
        entries=safe_json(state/'activity.json',[])
    return [x for x in entries if now-86400<=x['at']<=now+300][-20:]

def record_event(state,tool,event):
    # Drop cwd, transcript_path, prompts, tool arguments/results and every unknown field.
    if tool not in ('codex','claude') or not isinstance(event,dict) or event.get('hook_event_name') not in EVENTS:return
    sid=event.get('session_id')
    if not isinstance(sid,str) or not 1<=len(sid)<=200:return
    with locked(state,'activity'):
        salt_path=state/'activity-salt.json';salt=safe_json(salt_path).get('salt')
        if not salt:
            import secrets
            salt=secrets.token_hex(32);save(salt_path,{'salt':salt})
        identifier=hashlib.sha256((salt+'\0'+tool+'\0'+sid).encode()).hexdigest()
        items=[x for x in safe_json(state/'activity.json',[]) if x.get('id')!=identifier and x.get('at',0)>time.time()-86400]
        items.append({'id':identifier,'tool':tool,'event':event['hook_event_name'],'at':time.time()});save(state/'activity.json',items[-20:])

def enrollment_code(value):
    # Normalize only recognized surrounding paste wrappers; never alter code characters.
    edge=' \t\r\n\v\f\u00a0\u200b\ufeff\u2060'
    code=value.strip(edge) if isinstance(value,str) else ''
    if code.startswith('\x1b[200~') and code.endswith('\x1b[201~'):
        code=code[6:-6].strip(edge)
    if len(code)>=2 and code[0]==code[-1] and code[0] in ('"',"'",'`'):
        code=code[1:-1].strip(edge)
    if not code:raise LocalError('No enrollment code entered. In CatProof, open Local devices, create a fresh ten-minute enrollment code, then paste it here. Input is hidden.')
    if not re.fullmatch(r'[A-Za-z0-9_-]{30,100}',code):
        invalid=sum(c not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-' for c in code)
        raise LocalError('Invalid enrollment code format: received '+str(len(code))+' characters, '+str(invalid)+' outside the allowed character set. The code itself is not displayed or sent. Use Copy enrollment code in Local devices, confirm Code copied, and paste with Command+V on macOS. Do not copy the surrounding instructions.')
    return code

def request(server,path,data,token=None,ca_file=None):
    headers={'Content-Type':'application/json','Accept':'application/json','User-Agent':'CatProof-Local/'+VERSION}
    if token:headers['Authorization']='Bearer '+token
    req=Request(origin(server)+path,data=json.dumps(data).encode(),headers=headers,method='POST')
    try:
        # No redirects or ambient proxy credentials. Standard certificate verification stays enabled.
        opener=build_opener(ProxyHandler({}),NoRedirect(),HTTPSHandler(context=ssl.create_default_context(cafile=ca_file)))
        with opener.open(req,timeout=20) as r:
            raw=r.read(65537)
            if len(raw)>65536:raise LocalError('Unexpected response size')
            return json.loads(raw)
    except HTTPError as e:
        code=e.code;e.close()
        if code==401 and path=='/api/local/enroll':raise LocalError('Enrollment code was not accepted. It may have expired, been used or been cancelled. In CatProof, open Local devices and create a NEW ten-minute code. Paste that code at the hidden prompt, then run the collector only after Device enrolled appears.') from None
        if code==401:raise LocalError('Device credential expired or revoked. Revoke the old device in CatProof if needed, run forget, then enroll again with a fresh code.') from None
        if code==403:raise LocalError('CatProof rejected this request (HTTP 403). Check that --server is the CatProof application HTTPS origin. This is an access/origin rejection, not proof that the code expired.') from None
        if code==429:raise LocalError('Server rate limit; retry later') from None
        raise LocalError('CatProof request failed (HTTP '+str(code)+'); no secrets logged') from None
    except LocalError:raise
    except Exception:raise LocalError('CatProof connection failed; check HTTPS, network and server availability') from None

def report(state,send=request):
    with locked(state,'report'):
        c=config(state)
        payload={'schema':'catproof.local.v1','sequence':c.get('sequence',0)+1,'platform':{'Darwin':'macOS','Linux':'Linux','Windows':'Windows'}.get(platform.system(),'Other'),'tools':discover(include_config=c.get('include_config',False)),'sessions':activity(state)}
        # Persist sequence before transmission; an uncertain delivery is never replayed with the same number.
        c['sequence']=payload['sequence'];save(state/'connection.json',c)
        kwargs={'ca_file':c['ca_file']} if c.get('ca_file') else {}
        return send(c['server'],'/api/local/report',payload,c['token'],**kwargs)

def hook_definition(state,tool):
    command=' '.join(shlex.quote(str(x)) for x in (sys.executable,Path(__file__).resolve(),'--state-dir',state,'event','--tool',tool))
    return {'hooks':{event:[{'hooks':[{'type':'command','command':command,'timeout':3}]}] for event in sorted(EVENTS)}}

def install_hooks(state,tool,home=None,remove=False):
    home=Path.home() if home is None else Path(home)
    path=home/('.codex/hooks.json' if tool=='codex' else '.claude/settings.json')
    path.parent.mkdir(parents=True,exist_ok=True)
    original=safe_json(path)
    if not isinstance(original,dict) or not isinstance(original.get('hooks',{}),dict):raise LocalError('Existing hook settings are unsupported; merge manually')
    hooks=original.setdefault('hooks',{});definition=hook_definition(state,tool)
    marker='--state-dir '+shlex.quote(str(state))+' event --tool '+tool
    for event,groups in definition['hooks'].items():
        if not isinstance(hooks.get(event,[]),list):raise LocalError('Existing hook settings are unsupported; merge manually')
        kept=[]
        for group in hooks.get(event,[]):
            if not isinstance(group,dict) or not isinstance(group.get('hooks',[]),list):raise LocalError('Existing hook settings are unsupported; merge manually')
            others=[h for h in group.get('hooks',[]) if not (isinstance(h,dict) and marker in h.get('command','') and str(Path(__file__).resolve()) in h.get('command',''))]
            if others:kept.append({**group,'hooks':others})
        if not remove:kept+=groups
        hooks[event]=kept
    if path.exists():
        backup=path.with_name(path.name+'.catproof-backup-'+str(time.time_ns()))
        fd=os.open(backup,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as f:f.write(path.read_bytes())
    fd,temp=tempfile.mkstemp(dir=path.parent,prefix='.catproof-hooks-')
    try:
        with os.fdopen(fd,'w') as f:json.dump(original,f,indent=2);f.write('\n')
        os.replace(temp,path)
    finally:
        if os.path.exists(temp):os.unlink(temp)

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--state-dir',type=Path,default=Path.home()/'.local/share/catproof')
    sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('scan');p.add_argument('--include-config',action='store_true')
    p=sub.add_parser('enroll');p.add_argument('--server',required=True);p.add_argument('--ca-file',type=Path,help='Trusted CA certificate file (for local HTTPS)');p.add_argument('--include-config',action='store_true')
    p=sub.add_parser('run');p.add_argument('--once',action='store_true')
    p=sub.add_parser('event');p.add_argument('--tool',choices=['codex','claude'],required=True)
    for name in ('install-hooks','remove-hooks','hooks'):
        p=sub.add_parser(name);p.add_argument('--tool',choices=['codex','claude'],required=True)
    sub.add_parser('forget')
    args=parser.parse_args();state=args.state_dir.expanduser().resolve()
    try:
        if args.command=='scan':print(json.dumps({'tools':discover(include_config=args.include_config),'sends_data':False},indent=2))
        elif args.command=='enroll':
            server=origin(args.server);private_dir(state)
            if (state/'connection.json').exists():raise LocalError('Already enrolled; revoke the old device and run forget before reenrolling')
            print('Metadata that will be sent (no prompts, code, transcripts, credentials or paths):')
            print(json.dumps(discover(include_config=args.include_config),indent=2))
            if input('Type CONNECT to approve this metadata and enroll: ').strip()!='CONNECT':raise LocalError('Enrollment cancelled')
            ca_file=str(args.ca_file.expanduser().resolve()) if args.ca_file else None
            print('Create a NEW code in CatProof → Local devices → Create ten-minute enrollment code. It is single-use; paste only the code. Input below is hidden.')
            code=enrollment_code(getpass.getpass('One-time enrollment code (hidden): '));result=request(server,'/api/local/enroll',{'code':code},ca_file=ca_file)
            if not isinstance(result.get('token'),str) or not re.fullmatch(r'[A-Za-z0-9_-]{30,100}',result['token']):raise LocalError('Invalid enrollment response')
            save(state/'connection.json',dict(server=server,ca_file=ca_file,token=result['token'],device_id=result['device_id'],expires_at=result['expires_at'],sequence=0,include_config=args.include_config));print('Device enrolled. Credential saved privately; run the collector to send reports.')
        elif args.command=='run':
            while True:
                try:report(state);print('Metadata report accepted.',flush=True)
                except LocalError as e:
                    print(str(e),file=sys.stderr)
                    if args.once:return 1
                    if any(word in str(e) for word in ('expired','revoked','Enroll','private','HTTP 403')):return 1
                if args.once:break
                time.sleep(60)
        elif args.command=='event':
            try:
                raw=sys.stdin.buffer.read(262145)
                if len(raw)<=262144:record_event(state,args.tool,json.loads(raw))
            except Exception:pass # Hooks never block the coding session and never print hook inputs.
        elif args.command=='hooks':print(json.dumps(hook_definition(state,args.tool),indent=2))
        elif args.command in ('install-hooks','remove-hooks'):
            install_hooks(state,args.tool,remove=args.command=='remove-hooks');print('Hook settings updated; existing settings backed up. Restart the client. For Codex, review and trust these hooks through /hooks.')
        elif args.command=='forget':
            with locked(state,'report'):
                for name in ('connection.json','activity.json','activity-salt.json'):(state/name).unlink(missing_ok=True)
            print('Local credentials and activity removed. Revoke the device in CatProof and remove hooks separately.')
    except (LocalError,OSError,ValueError) as e:
        print(str(e) if isinstance(e,LocalError) else 'Local operation failed; no credentials logged.',file=sys.stderr);return 1
    except KeyboardInterrupt:return 0
    return 0
if __name__=='__main__':sys.exit(main())
