from network_policy import build_opener
"""Bounded HTTPS requests with same-origin pagination and no redirect credential forwarding."""
import json
from urllib.parse import urlsplit,urljoin,urlencode
from urllib.request import Request
from urllib.error import HTTPError
from registry import ConnectorError,NoRedirect

class Response:
    def __init__(self,data,headers=None,text=''):
        self.data=data;self.headers=headers or {};self.text=text

class Transport:
    def __init__(self,origin,headers=None):
        self.origin=origin.rstrip('/');self.headers=headers or {};self.calls=0
    def request(self,path,params=None,body=None,method='GET',headers=None,xml=False):
        self.calls+=1
        if self.calls>300:raise ConnectorError('Connector request limit reached; previous snapshot retained.')
        url=urljoin(self.origin+'/',path)
        a,b=urlsplit(self.origin),urlsplit(url)
        if (a.scheme,a.netloc)!=(b.scheme,b.netloc):raise ConnectorError('Cross-origin pagination or credential forwarding rejected.')
        if a.scheme!='https' and not (a.scheme=='http' and a.hostname in ('localhost','127.0.0.1')):raise ConnectorError('HTTPS required.')
        if a.username or a.password or b.fragment:raise ConnectorError('Invalid connector URL.')
        if params:url+=('&' if '?' in url else '?')+urlencode(params)
        h={'Accept':'application/json',**self.headers,**(headers or {})}
        payload=None
        if body is not None:
            payload=body.encode() if isinstance(body,str) else json.dumps(body).encode()
            h.setdefault('Content-Type','text/xml; charset=utf-8' if xml else 'application/json')
        try:
            req=Request(url,data=payload,method=method,headers=h)
            with build_opener(NoRedirect).open(req,timeout=20) as r:
                raw=r.read(5_000_001)
                if len(raw)>5_000_000:raise ConnectorError('Response size limit reached.')
                text=raw.decode('utf-8');data=None if xml else json.loads(text)
                return Response(data,dict(r.headers),text)
        except HTTPError as e:
            code=e.code;e.close();raise ConnectorError('Connector read failed (HTTP '+str(code)+'). Check credentials, permissions and scope.') from None
        except ConnectorError:raise
        except Exception:raise ConnectorError('Connector read failed. Check network, TLS and response format.') from None

def collection(data,key,allow_missing=False):
    if not isinstance(data,dict):raise ConnectorError('Invalid collection response.')
    if 'error' in data or data.get('unreachable') or data.get('errors'):raise ConnectorError('Source returned an error or incomplete scope.')
    if allow_missing and key not in data and set(data)-{'nextPageToken'}:raise ConnectorError('Collection field missing from response.')
    rows=data.get(key,[] if allow_missing else None)
    if not isinstance(rows,list) or any(not isinstance(r,dict) for r in rows):raise ConnectorError('Invalid '+key+' collection.')
    return rows

def continuation(data,key):
    """Only an absent/null/empty string cursor can mean the end of a list."""
    value=data.get(key)
    if value is None:return None
    if not isinstance(value,str):raise ConnectorError('Invalid pagination token; previous inventory retained.')
    return value or None

def pages(transport,path,key,params=None,token_name='pageToken',response_token='nextPageToken',allow_missing=False):
    params=dict(params or {});seen=set()
    for _ in range(100):
        data=transport.request(path,params).data
        yield from collection(data,key,allow_missing)
        token=continuation(data,response_token)
        if not token:return
        if not isinstance(token,str) or token in seen:raise ConnectorError('Invalid or repeated pagination token.')
        seen.add(token);params[token_name]=token
    raise ConnectorError('Pagination limit reached; inventory was not applied.')

def odata(transport,path):
    seen=set()
    for _ in range(100):
        data=transport.request(path).data
        yield from collection(data,'value')
        path=continuation(data,'@odata.nextLink')
        if not path:return
        if not isinstance(path,str) or path in seen:raise ConnectorError('Repeated or invalid page link.')
        seen.add(path)
    raise ConnectorError('Pagination limit reached.')
