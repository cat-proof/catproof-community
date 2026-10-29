"""Render entry point with personal accounts and workspace-scoped registry access."""
import hashlib
import hmac
import html
import json
import os
import signal
import threading
import time
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
from federation_app import FederationHandler, DEMO_FILES, demo_file
from hosted_store import HostedStore
from accounts import SESSION_SECONDS
from enterprise import RegistryAccounts,can_write
from network_policy import hosted_egress
from local_devices import LocalDevices,SlowReport
from account_recovery import AccountRecovery
from reset_email import ResendMailer,PasswordResetEmail
from connector_secrets import ConnectorSecrets
from website_leads import WebsiteLeads,validate_lead
from federation import DuplicateConnection,ConnectionLimitError,connection_limit_response
from marketing import ASSETS,PAGES,STATIC as BRAND_STATIC,asset,public_page,discovery_file

ACCOUNT_STATIC=Path(__file__).parent/'account_static'
PUBLIC_FILES={'/account.css':('style.css','text/css'),'/account.js':('app.js','text/javascript'),'/recovery.js':('recovery.js','text/javascript'),'/forgot-password.js':('forgot-password.js','text/javascript')}

class BoundedServer(ThreadingHTTPServer):
    daemon_threads=True
    request_queue_size=64
    def __init__(self,*args,**kwargs):self.capacity=threading.BoundedSemaphore(32);super().__init__(*args,**kwargs)
    def process_request(self,request,address):
        if not self.capacity.acquire(blocking=False):
            try:request.sendall(b'HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\nRetry-After: 5\r\n\r\n')
            finally:self.shutdown_request(request)
            return
        try:super().process_request(request,address)
        except Exception:self.capacity.release();raise
    def process_request_thread(self,request,address):
        try:super().process_request_thread(request,address)
        finally:self.capacity.release()

class HostedHandler(FederationHandler):
    def setup(self):
        super().setup();self.connection.settimeout(15)

    def action_origins(self):return self.server.origins
    def allowed(self):return self.headers.get('Host') in self.server.hosts
    def session_token(self):
        try:return SimpleCookie(self.headers.get('Cookie',''))['catproof_session'].value
        except (KeyError,ValueError):return ''
    def authenticated(self):
        self.principal=self.resolve_principal()
        return bool(self.principal)
    def resolve_principal(self,touch=True):
        authorization=self.headers.get('Authorization')
        if authorization is not None:
            if self.command not in ('GET','HEAD') or self.path!='/api/catalog' or not authorization.startswith('Bearer '):return None
            return self.server.accounts.resolve_peer(authorization[7:])
        return self.server.accounts.resolve(self.session_token(),touch=touch)
    def reply(self,code,payload,kind='application/json'):
        if getattr(self,'buffering',False):self.pending=(code,payload,kind);return
        super().reply(code,payload,kind)
    def redirect(self,path,cookie=None,code=303):
        self.send_response(code);self.send_header('Location',path)
        self.send_header('Cache-Control','no-store');self.send_header('Content-Length','0')
        if cookie:self.send_header('Set-Cookie',cookie)
        self.end_headers()
    def end_headers(self):
        if self.path=='/api/website-leads':
            self.send_header('Vary','Origin')
            if self.allowed() and self.headers.get('Origin') in self.server.lead_origins:
                self.send_header('Access-Control-Allow-Origin',self.headers['Origin'])
        path=urlsplit(self.path).path
        marketing=self.headers.get('Host') in self.server.marketing_hosts
        if (not marketing and path not in ASSETS and path!='/site.webmanifest') or path in DEMO_FILES or path.startswith(('/api/','/account/')) or path in ('/login','/signup','/healthz'):
            self.send_header('X-Robots-Tag','noindex, nofollow, noarchive')
        self.send_header('Strict-Transport-Security','max-age=31536000')
        self.send_header('Referrer-Policy','same-origin')
        self.send_header('Permissions-Policy','camera=(), microphone=(), geolocation=()')
        self.send_header('X-Frame-Options','DENY')
        super().end_headers()
    def session_cookie(self,token):
        return 'catproof_session='+token+'; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age='+str(SESSION_SECONDS)
    def page(self,name):
        body=(ACCOUNT_STATIC/name).read_text().replace('{{APP}}',html.escape(self.server.app_origin,quote=True))
        return self.reply(200,body.encode(),'text/html; charset=utf-8')
    def do_HEAD(self):self.do_GET()
    def do_GET(self):
        if self.path=='/healthz':
            try:
                if not self.server.persistence.healthy():raise RuntimeError()
                return self.reply(200,{'status':'ok','product':'CatProof','revision':os.getenv('RENDER_GIT_COMMIT','local')})
            except Exception:return self.reply(503,{'status':'unavailable'})
        if not self.allowed():return self.reply(403,{'error':'Unrecognized host'})
        path=urlsplit(self.path).path
        marketing=self.headers.get('Host') in self.server.marketing_hosts
        if path in ASSETS:
            body,kind=asset(path);return self.reply(200,body,kind)
        discovery=discovery_file(path,marketing)
        if discovery:
            body,kind=discovery;return self.reply(200,body,kind)
        if marketing and self.server.marketing_origin and 'https://'+self.headers.get('Host','')!=self.server.marketing_origin:
            return self.redirect(self.server.marketing_origin+self.path,code=301)
        if path in PAGES and (marketing or path!='/'):
            if not marketing and self.server.marketing_origin:return self.redirect(self.server.marketing_origin+path,code=301)
            return self.reply(200,public_page(path,self.server.app_origin).encode(),'text/html; charset=utf-8')
        if path=='/welcome':
            if self.server.marketing_origin:return self.redirect(self.server.marketing_origin+'/',code=301)
            return self.reply(200,public_page('/',self.server.app_origin).encode(),'text/html; charset=utf-8')
        if path in DEMO_FILES:
            body,kind=demo_file(path);return self.reply(200,body,kind)
        if path in PUBLIC_FILES:
            name,kind=PUBLIC_FILES[path];return self.reply(200,(ACCOUNT_STATIC/name).read_bytes(),kind)
        if path=='/recover':return self.page('recover.html')
        if path=='/forgot-password':return self.page('forgot-password.html')
        if path in ('/signup','/login'):
            if self.server.app_origin and 'https://'+self.headers.get('Host','')!=self.server.app_origin:
                return self.redirect(self.server.app_origin+path)
            try:
                if self.authenticated():return self.redirect('/')
            except Exception:return self.reply(503,{'error':'Account service is temporarily unavailable.'})
            return self.page('signup.html' if path=='/signup' else 'login.html')
        if marketing:return self.reply(404,b'<!doctype html><html lang="en"><head><meta name="robots" content="noindex"><title>Page not found | CatProof</title></head><body><h1>Page not found</h1><p><a href="/">Return to CatProof</a></p></body></html>','text/html; charset=utf-8')
        if self.path=='/style.css':return super().do_GET()
        try:
            if not self.authenticated():
                if self.path.startswith('/api/'):return self.reply(401,{'error':'Sign in required'})
                return self.redirect((self.server.app_origin or '')+'/login')
        except Exception:return self.reply(503,{'error':'Account service is temporarily unavailable.'})
        if self.path=='/api/local/devices':
            try:return self.reply(200,self.server.local_devices.listing(self.principal))
            except PermissionError:return self.reply(403,{'error':'Registry membership changed'})
            except Exception:return self.reply(503,{'error':'Local device inventory unavailable'})
        if self.path in ('/local/collector.py','/local/guide.md'):
            name='local/collector.py' if self.path.endswith('.py') else 'LOCAL.md'
            return self.reply(200,(Path(__file__).parent/name).read_bytes(),'text/plain; charset=utf-8')
        if self.path=='/api/security/audit':
            if self.principal.get('role')!='owner':return self.reply(403,{'error':'Only owners can export the security audit.'})
            try:return self.reply(200,{'registry_id':self.principal['workspace_id'],'events':self.server.accounts.security(self.principal,self.session_token()).get('events',[])})
            except Exception:return self.reply(503,{'error':'Audit export unavailable.'})
        if self.path=='/api/security':
            try:return self.reply(200,dict(self.server.accounts.security(self.principal,self.session_token()),bootstrap_enabled=self.principal['workspace_id']=='legacy' and self.server.bootstrap_enabled and self.server.accounts.bootstrap_allowed(),bootstrap_account=self.principal['id'].startswith('legacy_') and '@' not in self.principal['email']))
            except PermissionError:return self.reply(403,{'error':'Access changed; sign in again.'})
            except Exception:return self.reply(503,{'error':'Security settings are temporarily unavailable.'})
        if self.path=='/api/me':
            prefix='CATPROOF_'+self.principal['workspace_id'].upper()+'_'
            return self.reply(200,dict(self.principal,credential_namespace=prefix if self.principal['workspace_id']!='legacy' and self.principal.get('role')=='owner' else '',plan='Community Edition · 2 saved connections per registry'))
        self.run_registry(False,super().do_GET)
    def form(self):
        length=int(self.headers.get('Content-Length','0'))
        if not 0<length<=10000:raise ValueError('Invalid request size')
        return {k:v[0] for k,v in parse_qs(self.rfile.read(length).decode()).items()}
    def rate_allowed(self,key):
        now=time.time();key=hashlib.sha256(key.strip().lower().encode()).hexdigest()
        with self.server.auth_lock:
            self.server.attempts=[t for t in self.server.attempts if now-t<300]
            self.server.auth_attempts={k:[t for t in values if now-t<600] for k,values in self.server.auth_attempts.items() if any(now-t<600 for t in values)}
            attempts=self.server.auth_attempts.setdefault(key,[])
            if len(self.server.attempts)>=100 or len(attempts)>=10:return False
            attempts.append(now);self.server.attempts.append(now)
        return self.server.accounts.rate_allowed(key)
    def do_OPTIONS(self):
        if self.path!='/api/website-leads':return self.reply(404,{'error':'Not found'})
        if not self.allowed() or self.headers.get('Origin') not in self.server.lead_origins:
            return self.reply(403,{'error':'Website origin required'})
        self.send_response(204)
        self.send_header('Access-Control-Allow-Methods','POST')
        self.send_header('Access-Control-Allow-Headers','Content-Type')
        self.send_header('Access-Control-Max-Age','600')
        self.send_header('Content-Length','0')
        self.end_headers()
    def capture_website_lead(self):
        if not self.allowed() or self.headers.get('Origin') not in self.server.lead_origins:
            return self.reply(403,{'error':'Website origin required'})
        try:
            if self.headers.get('Transfer-Encoding') or self.headers.get('Content-Type','').split(';')[0]!='application/json':raise ValueError('Invalid request format.')
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=32768:raise ValueError('Invalid request size.')
            data=json.loads(self.rfile.read(length))
            lead=validate_lead(data)
            if not self.server.website_leads.rate_allowed(lead['email'] if lead else 'honeypot'):
                return self.reply(429,{'error':'Too many submissions. Please try again in ten minutes.'})
            self.server.website_leads.capture(data)
            return self.reply(202,{'message':'Submission saved.'})
        except (ValueError,TypeError,UnicodeError) as exc:return self.reply(400,{'error':str(exc) if isinstance(exc,ValueError) and not isinstance(exc,json.JSONDecodeError) else 'Invalid submission.'})
        except Exception:return self.reply(503,{'error':'We could not save your details. Please try again or email info@catproof.ai.'})
    def do_POST(self):
        if self.path=='/api/website-leads':return self.capture_website_lead()
        if self.headers.get('Host') in self.server.marketing_hosts:return self.reply(404,{'error':'Use the application host for account and registry actions'})
        if self.path in ('/api/local/enroll','/api/local/report'):
            # These two endpoints use one-time pairing codes or report-only device credentials.
            # They never use cookies or accept browser-origin requests.
            if not self.allowed() or self.headers.get('Origin') is not None:return self.reply(403,{'error':'Collector requests require an allowed host and no browser Origin'})
            try:
                if self.headers.get('Transfer-Encoding') or self.headers.get('Content-Type','').split(';')[0]!='application/json':raise ValueError()
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=32768:raise ValueError()
                data=json.loads(self.rfile.read(length))
                if not isinstance(data,dict):raise ValueError()
                if self.path=='/api/local/enroll':
                    if set(data)!={'code'}:raise ValueError()
                    if not self.rate_allowed('local-enrollment'):return self.reply(429,{'error':'Too many enrollment attempts; retry in ten minutes'})
                    return self.reply(201,self.server.local_devices.enroll(data['code']))
                auth=self.headers.get('Authorization','')
                if not auth.startswith('Bearer '):raise PermissionError()
                return self.reply(200,self.server.local_devices.report(auth[7:],data))
            except SlowReport:
                self.send_response(429);self.send_header('Retry-After','30');self.send_header('Content-Length','0');self.end_headers();return
            except PermissionError:return self.reply(401,{'error':'Enrollment or device credential expired, revoked or rejected'})
            except (ValueError,TypeError,UnicodeError):return self.reply(400,{'error':'Invalid metadata report or enrollment request'})
            except Exception:return self.reply(503,{'error':'Local collection unavailable; retry later'})
        # Cookie-authenticated mutations must originate on this exact host.
        if not self.allowed() or self.headers.get('Origin')!='https://'+self.headers.get('Host',''):
            return self.reply(403,{'error':'Same-origin request required'})
        if self.path=='/account/forgot-password':
            try:
                if not self.server.password_reset.ready:raise RuntimeError('Password reset email is not configured. Contact the service owner for a private recovery link.')
                data=self.form();email=data.get('email','').strip().lower()
                if not self.rate_allowed('reset-ip:'+self.client_address[0]) or not self.rate_allowed('reset-email:'+email):return self.reply(429,{'error':'Too many requests. Retry in ten minutes.'})
                self.server.password_reset.request(email)
                return self.reply(202,{'message':'If an account exists for that email, you will receive a reset link shortly. Check your spam folder. If no email arrives, retry later or contact the service owner.'})
            except ValueError:return self.reply(400,{'error':'Enter a valid email address.'})
            except RuntimeError as exc:return self.reply(503,{'error':str(exc)})
            except Exception:return self.reply(503,{'error':'Password reset is temporarily unavailable. Please retry later.'})
        if self.path=='/account/recover':
            if not self.rate_allowed('account-recovery:'+self.client_address[0]):return self.reply(429,{'error':'Too many attempts. Retry in ten minutes.'})
            try:
                data=self.form();self.server.recovery.reset(data.get('email',''),data.get('token',''),data.get('new_password',''))
                return self.reply(200,{'ok':True})
            except PermissionError as exc:return self.reply(403,{'error':str(exc)})
            except ValueError as exc:return self.reply(400,{'error':str(exc)})
            except Exception:return self.reply(503,{'error':'Recovery unavailable. No success was recorded.'})
        if self.path in ('/login','/signup'):
            try:
                data=self.form();identity=data.get('username',data.get('email',''))
                if not self.rate_allowed(identity):return self.reply(429,{'error':'Too many attempts. Try again in ten minutes.'})
                if self.path=='/signup':result=self.server.accounts.register(data)
                else:
                    password=data.get('password','')
                    if len(password)>128:return self.redirect('/login?error=credentials')
                    if self.server.bootstrap_enabled and identity in self.server.users and hmac.compare_digest(password.encode(),self.server.secret):result=self.server.accounts.legacy_login(identity)
                    else:result=self.server.accounts.login(identity,password)
                    if not result:return self.redirect('/login?error=credentials')
                return self.redirect('/',self.session_cookie(result[1]))
            except (ValueError,UnicodeError) as exc:
                return self.redirect(self.path+'?error='+('exists' if 'already' in str(exc) else 'invalid'))
            except Exception:return self.reply(503,{'error':'Account service is temporarily unavailable. Retry; your registry has not been opened.'})
        try:
            if not self.authenticated():return self.reply(401,{'error':'Sign in required'})
            if self.path in ('/api/credentials/connect','/api/credentials/connect-lovable','/api/credentials/save','/api/credentials/remove'):
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=40000:raise ValueError()
                data=json.loads(self.rfile.read(length))
                try:result=self.server.credentials.change(self.principal,data,connect=self.path.endswith(('connect','connect-lovable')),remove=self.path.endswith('remove'))
                except ConnectionLimitError as exc:return self.reply(409,connection_limit_response(exc))
                except DuplicateConnection as exc:return self.reply(409,{'error':str(exc),'connection_id':exc.connection_id})
                except ValueError as exc:return self.reply(400,{'error':str(exc)})
                return self.reply(200,result)
            if self.path in ('/api/local/pair','/api/local/revoke','/api/local/cancel-pairing'):
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=10000:raise ValueError()
                data=json.loads(self.rfile.read(length))
                if not isinstance(data,dict) or any(not isinstance(x,str) for x in data.values()):raise ValueError()
                if self.path=='/api/local/pair':return self.reply(201,self.server.local_devices.pair(self.principal,data.get('label',''),data.get('password','')))
                self.server.local_devices.revoke(self.principal,data.get('id',''),data.get('password',''),pairing=self.path.endswith('cancel-pairing'))
                return self.reply(200,{'ok':True})
            if self.path=='/logout':
                self.server.accounts.revoke(self.session_token())
                return self.redirect('/login','catproof_session=; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=0')
            if self.path.startswith('/api/security/'):
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=10000:return self.reply(400,{'error':'Invalid request size'})
                data=json.loads(self.rfile.read(length))
                if not isinstance(data,dict) or any(not isinstance(value,str) for value in data.values()):raise ValueError()
                accounts=self.server.accounts;actor=self.principal;token=self.session_token()
                if self.path=='/api/security/invite':return self.reply(201,accounts.invite(actor,data.get('email',''),data.get('role','viewer'),data.get('password','')))
                elif self.path=='/api/security/peer-token':return self.reply(201,accounts.create_peer_token(actor,data.get('label',''),data.get('password','')))
                elif self.path=='/api/security/revoke-peer-token':accounts.revoke_peer_token(actor,data.get('id',''),data.get('password',''))
                elif self.path=='/api/security/accept':accounts.accept(actor,data.get('code',''),token)
                elif self.path=='/api/security/switch':accounts.switch(actor,data.get('registry_id',''),token)
                elif self.path=='/api/security/member':accounts.member_change(actor,data.get('user_id',''),data.get('role',''),data.get('password',''))
                elif self.path=='/api/security/cancel-invite':accounts.cancel_invite(actor,data.get('id',''),data.get('password',''))
                elif self.path=='/api/security/revoke-session':accounts.revoke_sessions(actor,data.get('id','others'),token)
                else:return self.reply(404,{'error':'Unknown security action'})
                return self.reply(200,{'ok':True})
            if self.path=='/account/upgrade':
                data=self.form()
                if not self.server.bootstrap_enabled or not hmac.compare_digest(data.get('current_password','').encode(),self.server.secret):raise PermissionError('Check your existing team password.')
                token=self.server.accounts.upgrade(self.principal,data)
                return self.redirect('/',self.session_cookie(token))
            if self.path=='/account/password':
                data=self.form();token=self.server.accounts.change_password(self.principal['id'],data.get('current_password',''),data.get('new_password',''))
                return self.redirect('/',self.session_cookie(token))
        except PermissionError as exc:return self.reply(403,{'error':str(exc)})
        except (ValueError,TypeError):return self.reply(400,{'error':'Check the request, invitation code and password. A registry must retain an owner.'})
        except Exception:return self.reply(503,{'error':'Account service is temporarily unavailable.'})
        if self.path in ('/api/refresh','/api/connection-preview') and hasattr(self.server.persistence,'refresh'):
            return self.refresh_registry()
        self.run_registry(True,super().do_POST)

    def authorize_refresh(self):
        current=self.resolve_principal(touch=False)
        if not current or current['workspace_id']!=self.principal['workspace_id'] or not can_write(current.get('role'),self.path):
            raise PermissionError('Session or registry access changed; sync result was not applied.')
        return current

    def refresh_registry(self):
        if self.headers.get('X-Registry-Action')!='refresh':return self.reply(403,{'error':'Registry action header required'})
        if not self.server.sync_capacity.acquire(blocking=False):return self.reply(429,{'error':'Sync workers are busy; retry shortly.'})
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=25000:raise ValueError('Invalid request size')
            data=json.loads(self.rfile.read(length))
            if not isinstance(data,dict) or set(data)-{'connection_id'} or ('connection_id' in data and not isinstance(data['connection_id'],str)):
                raise ValueError('Invalid refresh request')
            if self.path=='/api/connection-preview':
                if not data.get('connection_id'):raise ValueError('Choose a connection')
                result=self.server.persistence.preview(self.principal['workspace_id'],data['connection_id'],self.authorize_refresh)
            else:
                result=self.server.persistence.refresh(self.principal['workspace_id'],data.get('connection_id'),self.authorize_refresh)
            return self.reply(200,result)
        except ConnectionLimitError as exc:return self.reply(409,connection_limit_response(exc))
        except PermissionError as exc:return self.reply(403,{'error':str(exc)})
        except (ValueError,TypeError):return self.reply(400,{'error':'Connection is missing, paused, changed during the read, or returned invalid records. Check setup and try again.'})
        except Exception:return self.reply(503,{'error':'Source read failed. Check credentials, permissions and scope, then retry. Previous inventory is retained.'})
        finally:self.server.sync_capacity.release()
    def run_registry(self,write,action):
        self.buffering=True;self.pending=None
        try:
            with self.server.persistence.request(write,workspace_id=self.principal['workspace_id']) as store:
                self.request_store=store
                # Recheck membership after obtaining the same lock used for membership changes.
                current=self.resolve_principal(touch=False)
                if not current or current['workspace_id']!=self.principal['workspace_id']:self.reply(401,{'error':'Session or registry access changed. Sign in again.'})
                elif write and not can_write(current.get('role'),self.path):self.reply(403,{'error':'Your registry role does not allow this action.'})
                else:
                    store.actor=current['email']
                    try:
                        with hosted_egress():action()
                    finally:store.actor=''

        except Exception:self.pending=(503,{'error':'Registry storage is temporarily unavailable; retry. No success was recorded.'},'application/json')
        finally:self.buffering=False;self.request_store=None
        if self.pending:self.reply(*self.pending)

def create_server(env=os.environ,persistence=None,address=None,accounts=None):
    password=env.get('APP_PASSWORD','')
    if len(password)<12:raise ValueError('Set APP_PASSWORD to a strong password of at least 12 characters')
    origin=env.get('APP_ORIGIN') or ('https://'+env['RENDER_EXTERNAL_HOSTNAME'] if env.get('RENDER_EXTERNAL_HOSTNAME') else '')
    origins={x.strip().rstrip('/') for x in env.get('APP_ORIGINS',origin).split(',') if x.strip()}
    if not origins or any(urlsplit(x).scheme!='https' or not urlsplit(x).hostname or urlsplit(x).path or urlsplit(x).username or urlsplit(x).query or urlsplit(x).fragment for x in origins):raise ValueError('Configure HTTPS APP_ORIGIN / APP_ORIGINS')
    app_origin=env.get('APP_PUBLIC_ORIGIN','').rstrip('/')
    if app_origin and app_origin not in origins:raise ValueError('APP_PUBLIC_ORIGIN must be an allowed HTTPS origin')
    marketing_origin=env.get('MARKETING_PUBLIC_ORIGIN','').rstrip('/')
    if marketing_origin and marketing_origin not in origins:raise ValueError('MARKETING_PUBLIC_ORIGIN must be an allowed HTTPS origin')
    if persistence is None:
        if not env.get('DATABASE_URL'):raise ValueError('DATABASE_URL is required; ephemeral storage is disabled')
        persistence=HostedStore(env['DATABASE_URL'])
    if accounts is None:
        if not env.get('DATABASE_URL'):raise ValueError('DATABASE_URL is required for personal accounts')
        accounts=RegistryAccounts(env['DATABASE_URL'])
    server=BoundedServer(address or ('0.0.0.0',int(env.get('PORT','10000'))),HostedHandler)
    server.persistence=persistence;server.store=persistence.store;server.accounts=accounts
    server.website_leads=WebsiteLeads(accounts)
    # A narrow, cookie-free public endpoint; account mutation origins stay unchanged.
    server.lead_origins={'https://catproof.ai','https://www.catproof.ai'}
    server.sync_capacity=threading.BoundedSemaphore(4)
    server.local_devices=LocalDevices(accounts)
    server.recovery=AccountRecovery(accounts,env.get('CATPROOF_ACCOUNT_RECOVERY_GRANTS',''))
    server.password_reset=PasswordResetEmail(server.recovery,ResendMailer(env),app_origin or origin)
    server.credentials=ConnectorSecrets(accounts,env.get('CONNECTOR_ENCRYPTION_KEYS',''))
    persistence.credentials=server.credentials
    if hasattr(persistence,'remove_retired_sources'):
        # Counts only: no tenant IDs, source metadata or credentials enter logs.
        print('Retired-source cleanup: '+json.dumps(persistence.remove_retired_sources()),flush=True)
    server.marketing_origin=marketing_origin
    server.origins=origins;server.hosts={urlsplit(x).netloc for x in origins};server.app_origin=app_origin
    server.marketing_hosts={x.strip() for x in env.get('MARKETING_HOSTS','catproof.ai,www.catproof.ai').split(',') if x.strip()}
    server.bootstrap_enabled=env.get('APP_BOOTSTRAP_LOGIN_ENABLED','true').lower()=='true'
    server.secret=password.encode();server.users={x.strip() for x in env.get('TEAM_USERNAMES','neeraj,mark').split(',') if x.strip()}
    server.attempts=[];server.auth_attempts={};server.auth_lock=threading.Lock()
    return server

def main():
    server=create_server()
    def stop(*_):threading.Thread(target=server.shutdown,daemon=True).start()
    signal.signal(signal.SIGTERM,stop)
    print('CatProof hosted registry listening',flush=True)
    try:server.serve_forever()
    finally:server.server_close();server.store.db.close()

if __name__=='__main__':main()
