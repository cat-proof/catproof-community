"""Fetch/apply concurrency, authorization and hard-deadline behavior."""
import subprocess
import tempfile
import time
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from enterprise import can_write
from registry import ConnectorError
from sync_worker import fetch_snapshot
import test_enterprise
from test_federation import payload, record


class HostedSyncTests(unittest.TestCase):
    setUp = test_enterprise.EnterpriseTests.setUp
    tearDown = test_enterprise.EnterpriseTests.tearDown
    signup = test_enterprise.EnterpriseTests.signup
    join = test_enterprise.EnterpriseTests.join

    def connection(self):
        with self.store.request(True, self.a['workspace_id']) as registry:
            return registry.add_connection({'name': 'Source', 'kind': 'databricks',
                'url': 'https://vendor.example', 'credential_prefix': 'DB'})

    def authorize(self, token=None):
        actor = self.accounts.resolve(token or self.at, touch=False)
        if not actor or actor['workspace_id'] != self.a['workspace_id'] or not can_write(actor['role'], '/api/refresh'):
            raise PermissionError('Access changed')
        return actor

    def start_sync(self, cid, token=None):
        entered, release = threading.Event(), threading.Event()
        results, errors = [], []
        def fetch(*args):
            entered.set()
            if not release.wait(5):
                raise RuntimeError('Test did not release fetch')
            return payload(record(name='Slow result'))
        def sync():
            try:
                results.append(self.store.refresh(self.a['workspace_id'], cid, lambda: self.authorize(token), fetch))
            except Exception as exc:
                errors.append(exc)
        thread = threading.Thread(target=sync)
        thread.start()
        self.addCleanup(lambda: (release.set(), thread.join(6)))
        self.assertTrue(entered.wait(3), errors)
        return release, thread, results, errors

    def test_blocked_provider_does_not_block_registry_reads_or_edits(self):
        cid = self.connection()
        release, thread, results, errors = self.start_sync(cid)
        done = threading.Event()
        def edit():
            with self.store.request(True, self.b['workspace_id']) as registry:
                registry.create({'name': 'Other tenant'})
            with self.store.request(True, self.a['workspace_id']) as registry:
                registry.create({'name': 'Same tenant edit'})
            done.set()
        editor = threading.Thread(target=edit)
        editor.start()
        self.addCleanup(lambda: editor.join(6))
        try:
            self.assertTrue(done.wait(2), 'An unrelated request waited for the provider')
        finally:
            release.set(); thread.join(6); editor.join(6)
        self.assertEqual(errors, [])
        self.assertEqual({a['name'] for a in results[0]['agents']}, {'Slow result', 'Same tenant edit'})

    def test_pause_then_enable_discards_inflight_result(self):
        cid = self.connection()
        release, thread, _, errors = self.start_sync(cid)
        with self.store.request(True, self.a['workspace_id']) as registry:
            registry.set_enabled(cid, False)
            registry.set_enabled(cid, True)
        release.set(); thread.join(6)
        self.assertEqual(errors, [])
        with self.store.request(False, self.a['workspace_id']) as registry:
            self.assertEqual(registry.snapshot()['agents'], [])

    def test_newer_sync_wins_even_if_older_fetch_finishes_last(self):
        cid = self.connection()
        release, thread, _, errors = self.start_sync(cid)
        self.store.refresh(self.a['workspace_id'], cid, self.authorize,
            lambda *args: payload(record(name='New result')))
        release.set(); thread.join(6)
        self.assertEqual(errors, [])
        with self.store.request(False, self.a['workspace_id']) as registry:
            self.assertEqual([r['name'] for r in registry.snapshot()['agents']], ['New result'])

    def test_demotion_during_fetch_prevents_apply(self):
        self.join('editor')
        cid = self.connection()
        release, thread, _, errors = self.start_sync(cid, self.bt)
        self.accounts.member_change(self.a, self.b['id'], 'viewer', 'fixture-personal-password')
        release.set(); thread.join(6)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], PermissionError)
        with self.store.request(False, self.a['workspace_id']) as registry:
            self.assertEqual(registry.snapshot()['agents'], [])

    def test_failure_retains_evidence_and_records_failed_run(self):
        cid = self.connection()
        self.store.refresh(self.a['workspace_id'], cid, self.authorize, lambda *args: payload(record()))
        def fail(*args):
            raise ConnectorError('Sync deadline exceeded')
        result = self.store.refresh(self.a['workspace_id'], cid, self.authorize, fail)
        self.assertTrue(result['agents'][0]['stale'])
        self.assertEqual(result['runs'][0]['outcome'], 'failed')
        self.assertEqual(result['runs'][0]['observed'], 0)

    def test_signout_during_fetch_prevents_apply(self):
        cid = self.connection()
        release, thread, _, errors = self.start_sync(cid)
        self.accounts.revoke(self.at)
        release.set(); thread.join(6)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], PermissionError)
        with self.store.request(False, self.a['workspace_id']) as registry:
            self.assertEqual(registry.snapshot()['agents'], [])

    def test_new_connection_preserves_existing_records(self):
        with self.store.request(True, self.a['workspace_id']) as registry:
            registry.create({'name': 'Before upgrade'})
        self.connection()
        with self.store.request(False, self.a['workspace_id']) as registry:
            self.assertEqual(registry.snapshot()['agents'][0]['name'], 'Before upgrade')


class WorkerTests(unittest.TestCase):
    def test_real_stalled_child_is_terminated_at_deadline(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory)/'blocked.py'
            script.write_text('import time\ntime.sleep(60)\n')
            started = time.monotonic()
            with patch('sync_worker.__file__', str(script)), self.assertRaisesRegex(ConnectorError, 'deadline'):
                fetch_snapshot({}, {}, .2)
            self.assertLess(time.monotonic() - started, 3)

    def test_timeout_kills_worker_without_exposing_credentials(self):
        with patch('sync_worker.subprocess.run', side_effect=subprocess.TimeoutExpired('worker', .1)) as run:
            with self.assertRaisesRegex(ConnectorError, 'deadline'):
                fetch_snapshot({'kind': 'atlas'}, {'TOKEN': 'synthetic-secret'}, .1)
        args, kwargs = run.call_args
        self.assertNotIn('synthetic-secret', str(args))
        self.assertNotIn('TOKEN', kwargs['env'])
        self.assertEqual(kwargs['timeout'], .1)

    def test_worker_executes_real_seeded_reader(self):
        result = fetch_snapshot({'kind': 'seeded', 'credential_prefix': ''}, {}, 5)
        self.assertTrue(result['agents'])
        self.assertTrue(all(r['provenance'] == 'seeded' for r in result['agents']))
