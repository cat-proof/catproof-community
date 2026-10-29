"""Exercise the Compose HTTPS app, including persistence after an optional restart.

Creates two synthetic local accounts; never prints their passwords or tokens.
"""
import argparse
import json
import secrets
import ssl
import subprocess
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPSHandler, HTTPRedirectHandler

ORIGIN = 'https://localhost:8443'


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ca', required=True, help='Exported local Caddy root certificate')
    parser.add_argument('--restart', action='store_true', help='Restart only the Compose app to verify durability')
    args = parser.parse_args()
    context = ssl.create_default_context(cafile=args.ca)
    opener = build_opener(HTTPSHandler(context=context), NoRedirect())

    def request(path, body=None, cookie=None, token=None, form=False):
        headers = {'Origin': ORIGIN, 'X-Registry-Action': 'refresh'}
        if cookie:headers['Cookie'] = cookie
        if token:headers['Authorization'] = 'Bearer ' + token
        headers['Content-Type'] = 'application/x-www-form-urlencoded' if form else 'application/json'
        data = (urlencode(body).encode() if form else json.dumps(body).encode()) if body is not None else None
        try:response = opener.open(Request(ORIGIN + path, data=data, headers=headers), timeout=10)
        except HTTPError as exc:response = exc
        with response:
            raw = response.read()
            value = json.loads(raw) if response.headers.get('Content-Type', '').startswith('application/json') else raw
            return response.code, response.headers, value

    assert request('/healthz')[0] == 200
    assert request('/api/inventory')[0] == 401
    cookies = []
    passwords = []
    suffix = secrets.token_hex(6)
    for index in range(2):
        password = secrets.token_urlsafe(24)
        status, headers, _ = request('/signup', {'name': 'Compose smoke',
            'email': f'compose-{suffix}-{index}@example.test', 'workspace': 'Local smoke fixture',
            'password': password}, form=True)
        assert status == 303, status
        assert all(flag in headers['Set-Cookie'] for flag in ('Secure', 'HttpOnly', 'SameSite=Strict'))
        cookies.append(headers['Set-Cookie'].split(';')[0]);passwords.append(password)
    status, _, result = request('/api/agents', {'name': 'Compose durability fixture'}, cookie=cookies[0])
    assert status == 201, status
    identifier = result['created_id']
    assert request('/api/inventory', cookie=cookies[1])[2]['agents'] == []
    status, _, peer = request('/api/security/peer-token', {'label': 'Compose smoke', 'password': passwords[0]}, cookie=cookies[0])
    assert status == 201, status
    assert request('/api/catalog', token=peer['token'])[2]['agents'][0]['id'] == identifier
    assert request('/api/inventory', token=peer['token'])[0] == 401
    assert request('/api/agents', {'name': 'Must fail'}, token=peer['token'])[0] == 401
    assert request('/api/security/revoke-peer-token', {'id': peer['id'], 'password': passwords[0]}, cookie=cookies[0])[0] == 200
    assert request('/api/catalog', token=peer['token'])[0] == 401
    status, _, connection = request('/api/connections', {'name': 'Seeded smoke', 'kind': 'seeded'}, cookie=cookies[0])
    assert status == 201, status
    status, _, synced = request('/api/refresh', {'connection_id': connection['created_connection_id']}, cookie=cookies[0])
    assert status == 200 and synced['runs'][0]['outcome'] == 'succeeded', status
    if args.restart:
        subprocess.run(['docker', 'compose', 'restart', 'app'], cwd=Path(__file__).resolve().parents[1], check=True, timeout=30)
        for _ in range(30):
            try:
                if request('/healthz')[0] == 200:break
            except (OSError, URLError):pass
            time.sleep(1)
        else:raise RuntimeError('App did not become healthy after restart')
    status, _, inventory = request('/api/inventory', cookie=cookies[0])
    assert status == 200 and any(a['registry_id'] == identifier for a in inventory['agents'])
    assert inventory['runs'][0]['outcome'] == 'succeeded'
    assert request('/api/catalog', token=peer['token'])[0] == 401
    for cookie in cookies:
        assert request('/logout', {}, cookie=cookie)[0] == 303
    print('PASS: verified HTTPS, signup, secure cookies, tenant isolation, peer scope/revocation, worker sync, saved inventory' + (' and restart durability.' if args.restart else '.'))
    print('Two synthetic accounts remain only in the local development database; no provider was contacted.')


if __name__ == '__main__':
    main()
