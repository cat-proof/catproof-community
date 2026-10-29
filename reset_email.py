"""Password reset email delivery; provider secrets and reset tokens never enter logs."""
import json
import re
import threading
from email.utils import parseaddr
from urllib.parse import urlencode
from urllib.request import Request,build_opener,HTTPRedirectHandler,ProxyHandler

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

class ResendMailer:
    def __init__(self,env):
        self.key=env.get('RESEND_API_KEY','');self.sender=env.get('PASSWORD_RESET_FROM','')
        address=parseaddr(self.sender)[1]
        self.ready=bool(self.key and '\n' not in self.key and '\r' not in self.key and '\n' not in self.sender and '\r' not in self.sender and re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',address))
    def send(self,email,link):
        if not self.ready:raise RuntimeError('Email sender unavailable')
        body=json.dumps({'from':self.sender,'to':[email],'subject':'Reset your CatProof password','text':'A password reset was requested for your CatProof account.\n\nOpen this private link to choose a new password:\n'+link+'\n\nThis link expires in 30 minutes and works once. If you did not request it, ignore this email. Your password has not changed.'}).encode()
        req=Request('https://api.resend.com/emails',data=body,headers={'Authorization':'Bearer '+self.key,'Content-Type':'application/json','User-Agent':'CatProof-password-recovery/1'})
        with build_opener(ProxyHandler({}),NoRedirect()).open(req,timeout=15) as response:
            if response.status not in (200,201,202):raise RuntimeError('Email delivery rejected')

class PasswordResetEmail:
    def __init__(self,recovery,mailer,origin):
        self.recovery=recovery;self.mailer=mailer;self.origin=origin;self.capacity=threading.BoundedSemaphore(4)
    @property
    def ready(self):return self.mailer.ready and self.origin.startswith('https://')
    def request(self,email):
        if not self.ready:raise RuntimeError('Password reset email is not configured. Contact the service owner for a private recovery link.')
        if not isinstance(email,str) or len(email)>254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email):raise ValueError('Enter a valid email address.')
        if not self.capacity.acquire(blocking=False):raise RuntimeError('Password reset is busy. Please retry shortly.')
        # Dispatch every valid address identically, including unknown accounts. Do not
        # wait for provider I/O or disclose account existence in status or timing.
        try:threading.Thread(target=self.deliver,args=(email.strip().lower(),),daemon=True).start()
        except Exception:self.capacity.release();raise RuntimeError('Password reset is busy. Please retry shortly.') from None
    def deliver(self,email):
        try:
            token=self.recovery.issue(email)
            if token:self.mailer.send(email,self.origin+'/recover#'+urlencode({'token':token,'email':email}))
        except Exception:print('Password reset delivery failed; no credential data logged',flush=True)
        finally:self.capacity.release()
