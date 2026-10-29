"""Real hosted HTTP requests, including a peer read consumed by the connector."""
import json
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, build_opener

from federation import discover
from hosted_app import create_server
from test_accounts_http import NoRedirect
import test_enterprise
from test_federation import payload, record


class PeerHTTPTests(unittest.TestCase):
    signup = test_enterprise.EnterpriseTests.signup

    def setUp(self):
        test_enterprise.EnterpriseTests.setUp(self)
        self.start_http()

    def start_http(self):
        self.server = create_server({'APP_PASSWORD': 'fixture-bootstrap-password',
            'APP_ORIGINS': 'https://registry.example', 'APP_BOOTSTRAP_LOGIN_ENABLED': 'false'},
            self.store, ('127.0.0.1', 0), self.accounts)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = 'http://127.0.0.1:' + str(self.server.server_port)
        self.opener = build_opener(NoRedirect())

    def stop_http(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()

    def tearDown(self):
        self.stop_http()
        test_enterprise.EnterpriseTests.tearDown(self)

    def request(self, path, body=None, token=None, cookie=None):
        headers = {'Host': 'registry.example', 'Origin': 'https://registry.example',
                   'Content-Type': 'application/json', 'X-Registry-Action': 'refresh'}
        if token:headers['Authorization'] = 'Bearer ' + token
        if cookie:headers['Cookie'] = 'catproof_session=' + cookie
        data = json.dumps(body).encode() if body is not None else None
        try:return self.opener.open(Request(self.url + path, headers=headers, data=data), timeout=5)
        except HTTPError as error:return error

    def issue(self):
        with self.request('/api/security/peer-token', {'label': 'Consumer', 'password': 'fixture-personal-password'}, cookie=self.at) as response:
            self.assertEqual(response.code, 201)
            return json.load(response)

    def test_peer_is_catalog_only_and_bound_to_issuing_registry(self):
        with self.store.request(True, self.a['workspace_id']) as registry:
            registry.create({'name': 'Published A'})
        with self.store.request(True, self.b['workspace_id']) as registry:
            registry.create({'name': 'Private B'})
        peer = self.issue()
        with self.request('/api/catalog', token=peer['token'], cookie=self.bt) as response:
            self.assertEqual(response.code, 200)
            self.assertEqual([a['name'] for a in json.load(response)['agents']], ['Published A'])
        for path in ('/api/inventory', '/api/security', '/api/me', '/api/local/devices', '/api/export.csv'):
            with self.request(path, token=peer['token']) as response:self.assertEqual(response.code, 401)
        for path in ('/api/agents', '/api/connections', '/api/refresh', '/api/security/peer-token'):
            with self.request(path, {}, token=peer['token'], cookie=self.at) as response:self.assertEqual(response.code, 401)
        with self.request('/api/catalog', token=self.at) as response:self.assertEqual(response.code, 401)
        with self.request('/api/security/revoke-peer-token', {'id': peer['id'], 'password': 'fixture-personal-password'}, cookie=self.at) as response:self.assertEqual(response.code, 200)
        with self.request('/api/catalog', token=peer['token']) as response:self.assertEqual(response.code, 401)

    def test_connector_reads_authenticated_peer_and_preserves_stale_evidence(self):
        with self.store.request(True, self.a['workspace_id']) as registry:
            cid = registry.add_connection({'name': 'Vendor', 'kind': 'databricks', 'url': 'https://vendor.example', 'credential_prefix': 'DB'})
            registry.ingest(cid, payload(record()))
            registry.apply_refresh(dict(registry.db.execute('SELECT * FROM catproof_connections WHERE id=?', (cid,)).fetchone()), None, 'Fixture failure', '2026-10-27T18:00:00Z')
        peer = self.issue()
        # Route the fixture hostname to this HTTP server; authentication and response
        # parsing remain real. Public egress/TLS are independently tested.
        test = self
        class FixtureRoute:
            def open(self, request, **kwargs):
                return test.opener.open(Request(test.url + '/api/catalog',
                    headers={'Host': 'registry.example', 'Authorization': request.get_header('Authorization')}), timeout=5)
        connection = {'kind': 'atlas', 'url': 'https://registry.example', 'credential_prefix': 'PEER', 'source_filter': ''}
        with patch('federation.build_opener', return_value=FixtureRoute()):
            result = discover(connection, {'PEER_TOKEN': peer['token']})
        with self.store.request(True, self.b['workspace_id']) as registry:
            cid = registry.add_connection({'name': 'Peer', **{k:v for k,v in connection.items() if k!='source_filter'}})
            registry.ingest(cid, result)
            row = registry.snapshot()['agents'][0]
            self.assertTrue(row['stale'])
            self.assertEqual(row['last_seen'], record()['last_seen'])

    def test_http_refresh_uses_worker_and_persists_result(self):
        with self.request('/api/connections', {'name': 'Fixture', 'kind': 'seeded'}, cookie=self.at) as response:
            self.assertEqual(response.code, 201)
            cid = json.load(response)['created_connection_id']
        with self.request('/api/refresh', {'connection_id': cid}, cookie=self.at) as response:
            self.assertEqual(response.code, 200)
            result = json.load(response)
            self.assertEqual(result['runs'][0]['outcome'], 'succeeded')
            self.assertTrue(result['agents'])
        with self.request('/api/inventory', cookie=self.at) as response:
            self.assertEqual(len(json.load(response)['agents']), len(result['agents']))

    def test_saturated_sync_pool_rejects_sync_but_keeps_reads_available(self):
        for _ in range(4):self.server.sync_capacity.acquire()
        try:
            with self.request('/api/refresh', {}, cookie=self.at) as response:self.assertEqual(response.code, 429)
            with self.request('/api/inventory', cookie=self.at) as response:self.assertEqual(response.code, 200)
        finally:
            for _ in range(4):self.server.sync_capacity.release()
