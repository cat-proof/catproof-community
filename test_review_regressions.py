from tests.db import registry as FederatedRegistry
"""Behavioral regressions from the hosted isolation/federation review."""
import unittest
from unittest.mock import patch

from federation import  discover
from hosted_store import HostedStore
from registry import ConnectorError
from test_federation import record, payload
from test_hosted import FakeDatabase


class CredentialIsolationTests(unittest.TestCase):
    def test_legacy_can_only_use_explicitly_allowed_provider_prefixes(self):
        database = FakeDatabase()
        store = HostedStore('fixture', database.connect)
        self.addCleanup(store.store.db.close)
        customer = 'CATPROOF_WS_' + 'A' * 32 + '_SF_TOKEN'
        environment = {
            customer: 'customer-secret', 'DB_TOKEN': 'legacy-secret',
            'UNRELATED_TOKEN': 'unrelated-secret', 'DATABASE_URL': 'private-db',
            'CATPROOF_LEGACY_CREDENTIAL_PREFIXES': 'DB',
        }
        with patch.dict('os.environ', environment, clear=True):
            with store.request(True, 'legacy') as registry:
                self.assertEqual(registry.env, {'DB_TOKEN': 'legacy-secret'})
                cid = registry.add_connection({'name': 'Probe', 'kind': 'atlas',
                    'url': 'https://receiver.example', 'credential_prefix': customer[:-6]})
                connection = dict(registry.db.execute('SELECT * FROM catproof_connections WHERE id=?', (cid,)).fetchone())
                with patch('federation.build_opener') as opener:
                    try:
                        discover(connection, registry.env)
                    except Exception:
                        pass
                    request = opener.return_value.open.call_args.args[0]
                    self.assertIsNone(request.get_header('Authorization'))

    def test_legacy_defaults_to_no_credentials(self):
        store = HostedStore('fixture', FakeDatabase().connect)
        self.addCleanup(store.store.db.close)
        with patch.dict('os.environ', {'SF_TOKEN': 'secret'}, clear=True):
            with store.request() as registry:
                self.assertEqual(registry.env, {})

    def test_legacy_allowlist_cannot_include_reserved_customer_namespace(self):
        from hosted_store import credential_environment
        prefix = 'CATPROOF_WS_' + 'A'*32 + '_SF'
        with patch.dict('os.environ', {'CATPROOF_LEGACY_CREDENTIAL_PREFIXES': prefix,
                prefix+'_TOKEN': 'customer-secret'}, clear=True):
            self.assertEqual(credential_environment('legacy'), {})


class FederationFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.upstream = FederatedRegistry(':memory:', env={})
        self.downstream = FederatedRegistry(':memory:', env={})
        self.addCleanup(self.upstream.db.close)
        self.addCleanup(self.downstream.db.close)
        self.source = self.upstream.add_connection({'name': 'Vendor', 'kind': 'databricks',
            'url': 'https://vendor.example', 'credential_prefix': 'DB'})
        self.peer = self.downstream.add_connection({'name': 'Peer', 'kind': 'atlas', 'url': 'https://peer.example'})
        self.upstream.ingest(self.source, payload(record()))

    def test_source_failure_remains_stale_after_federation(self):
        def fail(*args):
            raise ConnectorError('Fixture access failure')
        self.upstream.reader = fail
        self.upstream.refresh(self.source)
        catalog = self.upstream.catalog()
        self.assertEqual(catalog['sources'][0]['health'], 'degraded')
        self.downstream.ingest(self.peer, catalog)
        row = self.downstream.snapshot()['agents'][0]
        self.assertTrue(row['stale'])
        self.assertEqual(row['last_seen'], record()['last_seen'])

    def test_not_observed_survives_federation_and_recovery(self):
        self.downstream.ingest(self.peer, self.upstream.catalog())
        self.upstream.ingest(self.source, payload())
        self.downstream.ingest(self.peer, self.upstream.catalog())
        self.assertFalse(self.downstream.snapshot()['agents'][0]['present'])
        self.downstream.ingest(self.peer, self.upstream.catalog())
        kinds = [e['kind'] for e in self.downstream.snapshot()['events']]
        self.assertEqual(kinds.count('not_observed'), 1)
        self.assertNotIn('rediscovered', kinds)
        self.upstream.ingest(self.source, payload(record()))
        self.downstream.ingest(self.peer, self.upstream.catalog())
        self.assertTrue(self.downstream.snapshot()['agents'][0]['present'])
        self.assertFalse(self.downstream.snapshot()['agents'][0]['stale'])

    def test_manual_catalog_records_are_not_claimed_as_vendor_observations(self):
        self.upstream.create({'name': 'Independent'})
        row = next(r for r in self.upstream.catalog()['agents'] if r['name'] == 'Independent')
        self.assertIsNone(row['observed_at'])
        self.assertIn('catalog_read_at', row)
        self.assertEqual(row['provenance'], 'manual')

    def test_legacy_peer_without_freshness_contract_is_marked_stale(self):
        import io
        import json
        old = {'schema_version': 'atlas.registry.v1', **payload(record())}
        connection = {'kind':'atlas','credential_prefix':'','url':'https://peer.example','source_filter':''}
        with patch('federation.build_opener') as opener:
            opener.return_value.open.return_value = io.BytesIO(json.dumps(old).encode())
            result = discover(connection, {})
        self.assertTrue(result['agents'][0]['stale'])

    def test_degraded_peer_cannot_omit_freshness_fields(self):
        import io
        import json
        incomplete = {'schema_version':'atlas.registry.v1', 'evidence_version':1,
                      'agents':[record()], 'sources':[{'name':'CatProof','health':'degraded'}]}
        connection = {'kind':'atlas','credential_prefix':'','url':'https://peer.example','source_filter':''}
        with patch('federation.build_opener') as opener:
            opener.return_value.open.return_value = io.BytesIO(json.dumps(incomplete).encode())
            with self.assertRaises(ConnectorError):discover(connection, {})
