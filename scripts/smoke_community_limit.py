"""Verify the running Community app's two-connection API limit without vendor reads."""
import argparse
import json
import secrets
import ssl
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPSHandler, HTTPRedirectHandler

ORIGIN='https://localhost:8443'


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args):
        return None


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ca',required=True)
    opener=build_opener(HTTPSHandler(context=ssl.create_default_context(cafile=parser.parse_args().ca)),NoRedirect())
    cookie=''

    def request(path,body=None,form=False):
        headers={'Origin':ORIGIN,'Content-Type':'application/x-www-form-urlencoded' if form else 'application/json','X-Registry-Action':'refresh'}
        if cookie:headers['Cookie']=cookie
        data=(urlencode(body).encode() if form else json.dumps(body).encode()) if body is not None else None
        try:response=opener.open(Request(ORIGIN+path,data=data,headers=headers),timeout=15)
        except HTTPError as exc:response=exc
        with response:
            raw=response.read()
            value=json.loads(raw) if response.headers.get('Content-Type','').startswith('application/json') else None
            return response.code,response.headers,value

    status,headers,_=request('/signup',{'name':'Community smoke','email':'limit-'+secrets.token_hex(8)+'@example.test','workspace':'Synthetic connection-limit fixture','password':secrets.token_urlsafe(24)},True)
    assert status==303,status
    cookie=headers['Set-Cookie'].split(';')[0]
    ids=[]
    for n,kind in enumerate(('a2a','mcp')):
        status,_,result=request('/api/connections',{'name':'Synthetic source '+str(n),'kind':kind,'url':'https://source'+str(n)+'.example.test'})
        assert status==201,(status,result)
        ids.append(result['created_connection_id'])
    status,_,result=request('/api/connection-state',{'connection_id':ids[0],'enabled':False})
    assert status==200 and result['connection_limits']['used']==2
    third={'name':'Synthetic third','kind':'a2a','url':'https://third.example.test'}
    for path,body in [('/api/connections',third),('/api/credentials/connect',dict(third,credentials={'mode':'none'}))]:
        status,_,result=request(path,body)
        assert status==409 and result['error_code']=='community_connection_limit',(status,result)
    status,_,result=request('/api/agents',{'name':'Retained manual record','business_owner':'Synthetic owner'})
    assert status==201
    agent_id=result['created_id']
    status,_,result=request('/api/connection-archive',{'connection_id':ids[0],'archived':True})
    assert status==200 and result['connection_limits']['used']==1
    assert any(a['registry_id']==agent_id for a in result['agents'])
    status,_,result=request('/api/credentials/connect',dict(third,credentials={'mode':'none'}))
    assert status==200,(status,result)
    status,_,result=request('/api/connection-archive',{'connection_id':ids[0],'archived':False})
    assert status==409 and result['error_code']=='community_connection_limit'
    assert request('/api/connection-archive',{'connection_id':ids[1],'archived':True})[0]==200
    status,_,result=request('/api/connection-archive',{'connection_id':ids[0],'archived':False})
    assert status==200
    source=next(s for s in result['sources'] if s['id']==ids[0])
    assert not source['enabled'] and result['connection_limits']['used']==2
    assert request('/logout',{})[0]==303
    print('PASS: two-connection cap, paused counting, both create APIs, archive/restore and retained records. No vendor API was contacted.')


if __name__=='__main__':
    main()
