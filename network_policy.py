"""Hosted urllib egress: HTTPS, public addresses, DNS pinning, no proxy bypass.

Local development keeps its explicitly supported loopback bridge behavior.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import http.client
import ipaddress
import socket
from urllib.request import HTTPSHandler,HTTPHandler,ProxyHandler,build_opener as original_opener
from urllib.error import URLError

HOSTED=ContextVar('catproof_hosted_egress',default=False)
@contextmanager
def hosted_egress():
    token=HOSTED.set(True)
    try:yield
    finally:HOSTED.reset(token)

def public_addresses(host,port):
    if port!=443:raise URLError('Hosted connections require HTTPS on port 443.')
    addresses=socket.getaddrinfo(host,port,type=socket.SOCK_STREAM)
    if not addresses:raise URLError('Source hostname has no address.')
    for _,_,_,_,address in addresses:
        ip=ipaddress.ip_address(address[0].split('%')[0])
        if not ip.is_global or (getattr(ip,'ipv4_mapped',None) and not ip.ipv4_mapped.is_global):raise URLError('Private and special-use network destinations are blocked.')
    return addresses
class PublicHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        addresses=public_addresses(self.host,self.port)
        # Pin the checked address; TLS still checks the original hostname.
        def pinned(address,timeout=socket._GLOBAL_DEFAULT_TIMEOUT,source_address=None):
            last=None
            for family,kind,proto,_,target in addresses:
                sock=socket.socket(family,kind,proto)
                try:
                    if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:sock.settimeout(timeout)
                    if source_address:sock.bind(source_address)
                    sock.connect(target);return sock
                except OSError as exc:last=exc;sock.close()
            raise last or OSError('Source connection failed')
        self._create_connection=pinned
        super().connect()
class PublicHTTPSHandler(HTTPSHandler):
    def https_open(self,request):return self.do_open(PublicHTTPSConnection,request,context=self._context)
class BlockHTTPHandler(HTTPHandler):
    def http_open(self,request):raise URLError('Hosted sources require HTTPS.')
def build_opener(*handlers):
    if HOSTED.get():return original_opener(ProxyHandler({}),PublicHTTPSHandler(),BlockHTTPHandler(),*handlers)
    return original_opener(*handlers)
