"""Database review regressions against disposable PostgreSQL, never providers."""
from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
import threading
import time
import unittest
from unittest.mock import patch

import psycopg
import sqlalchemy as sa
import accounts
from account_recovery import AccountRecovery
from database import HOSTED_HEAD, require_hosted_schema
from federation import FederatedRegistry
import test_account_recovery as recovery_tests
import test_migrations as migration_tests
import test_sqlite_import as sqlite_tests


class PasswordSerializationTests(unittest.TestCase):
    setUp=recovery_tests.RecoveryTests.setUp
    tearDown=recovery_tests.RecoveryTests.tearDown
    signup=recovery_tests.RecoveryTests.signup

    def race_recovery(self,change=False):
        recovery=AccountRecovery(self.accounts)
        grant=recovery.issue(self.a['email'])
        verified=threading.Event();release=threading.Event()
        original=accounts.password_matches
        def pause(password,encoded):
            valid=original(password,encoded)
            verified.set()
            if not release.wait(10):raise TimeoutError('Test did not release password verification')
            return valid
        def operation():
            if change:return self.accounts.change_password(self.a['id'],'fixture-personal-password','concurrent-change-password')
            return self.accounts.login(self.a['email'],'fixture-personal-password')[1]
        with ThreadPoolExecutor(max_workers=2) as pool,patch('accounts.password_matches',side_effect=pause):
            first=pool.submit(operation)
            try:
                self.assertTrue(verified.wait(5))
                reset=pool.submit(recovery.reset,self.a['email'],grant,'recovered-fixture-password')
                # Observe a real PostgreSQL lock wait, rather than assuming a thread
                # has reached the database after an arbitrary sleep.
                waiting=False;deadline=time.monotonic()+5
                with self.connect() as observer:
                    observer.autocommit=True  # Refresh pg_stat_activity on every poll.
                    while time.monotonic()<deadline and not reset.done():
                        waiting=observer.execute("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE datname=current_database() AND cardinality(pg_blocking_pids(pid))>0 AND query LIKE '%%catproof_users%%')").fetchone()[0]
                        if waiting:break
                        time.sleep(.01)
                self.assertTrue(waiting,'Recovery must wait for in-flight password verification')
            finally:release.set()
            issued=first.result(timeout=5);reset.result(timeout=5)
        self.assertIsNone(self.accounts.resolve(issued))
        self.assertIsNone(self.accounts.resolve(self.at))
        self.assertIsNone(self.accounts.login(self.a['email'],'fixture-personal-password'))
        self.assertIsNone(self.accounts.login(self.a['email'],'concurrent-change-password'))
        self.assertIsNotNone(self.accounts.login(self.a['email'],'recovered-fixture-password'))
        self.assertIsNotNone(self.accounts.resolve(self.bt))

    def test_recovery_serializes_with_login(self):self.race_recovery()
    def test_recovery_serializes_with_password_change(self):self.race_recovery(change=True)


class BlobValidationTests(unittest.TestCase):
    setUp=migration_tests.MigrationTests.setUp
    tearDown=migration_tests.MigrationTests.tearDown
    upgrade=migration_tests.MigrationTests.upgrade
    legacy_blob=migration_tests.MigrationTests.legacy_blob
    seed_legacy=migration_tests.MigrationTests.seed_legacy

    def assert_blob_rejected(self,statements):
        self.seed_legacy()
        with self.connect() as db:
            old=sqlite3.connect(':memory:')
            old.deserialize(bytes(db.execute("SELECT payload FROM catproof_workspace_state WHERE workspace_id='one'").fetchone()[0]))
            old.executescript(statements);old.commit();blob=old.serialize();old.close()
            db.execute("UPDATE catproof_workspace_state SET payload=%s WHERE workspace_id='one'",(blob,))
        with self.assertRaisesRegex(RuntimeError,'import failed'):self.upgrade()
        with self.connect() as db:
            self.assertEqual(bytes(db.execute("SELECT payload FROM catproof_workspace_state WHERE workspace_id='one'").fetchone()[0]),blob)
            self.assertIsNone(db.execute("SELECT to_regclass('catproof_connections')").fetchone()[0])

    def test_unknown_table_preserves_original_snapshot(self):
        self.assert_blob_rejected("CREATE TABLE extra_history(note TEXT); INSERT INTO extra_history VALUES('Keep me');")
    def test_unknown_empty_column_preserves_original_snapshot(self):
        self.assert_blob_rejected('DELETE FROM sync_runs; ALTER TABLE sync_runs ADD COLUMN future_field TEXT;')
    def test_unknown_view_preserves_original_snapshot(self):
        self.assert_blob_rejected('CREATE VIEW future_view AS SELECT * FROM observations;')
    def test_unknown_trigger_preserves_original_snapshot(self):
        self.assert_blob_rejected('CREATE TRIGGER future_trigger AFTER INSERT ON entities BEGIN UPDATE entities SET notes=\'trigger\'; END;')
    def test_missing_binding_preserves_original_snapshot(self):
        self.assert_blob_rejected('DELETE FROM bindings;')


class FileValidationTests(unittest.TestCase):
    setUp=sqlite_tests.SQLiteImportTests.setUp
    source=sqlite_tests.SQLiteImportTests.source
    run_import=sqlite_tests.SQLiteImportTests.run_import
    assert_empty=sqlite_tests.SQLiteImportTests.assert_empty

    def test_missing_binding_refuses_entire_import(self):
        self.source()
        with sqlite3.connect(self.path) as old:old.execute('DELETE FROM bindings')
        before=self.path.read_bytes()
        with self.assertRaisesRegex(ValueError,'binding'):self.run_import()
        self.assert_empty();self.assertEqual(self.path.read_bytes(),before)


class SchemaIntegrityTests(unittest.TestCase):
    setUp=migration_tests.MigrationTests.setUp
    tearDown=migration_tests.MigrationTests.tearDown
    upgrade=migration_tests.MigrationTests.upgrade

    def test_foreign_alembic_history_remains_untouched(self):
        with self.connect() as db:
            db.execute('CREATE TABLE alembic_version(version_num VARCHAR(32) PRIMARY KEY)')
            db.execute("INSERT INTO alembic_version VALUES('other_app_revision')")
        self.upgrade();self.upgrade()
        with self.connect() as db:
            require_hosted_schema(db)
            self.assertEqual(db.execute('SELECT version_num FROM alembic_version').fetchall(),[('other_app_revision',)])
            self.assertEqual(db.execute('SELECT version_num FROM catproof_alembic_version').fetchall(),[(HOSTED_HEAD,)])

    def test_existing_catproof_history_is_adopted_and_data_preserved(self):
        self.upgrade('h0004')
        with self.connect() as db:
            db.execute('ALTER TABLE catproof_alembic_version RENAME TO alembic_version')
            db.execute("INSERT INTO catproof_entities(tenant_id,id,name,created_at,updated_at) VALUES('legacy','keep','Keep',now(),now())")
        self.upgrade();self.upgrade()
        with self.connect() as db:
            require_hosted_schema(db)
            self.assertEqual(db.execute('SELECT id FROM catproof_entities').fetchall(),[('keep',)])
            self.assertIsNone(db.execute("SELECT to_regclass('alembic_version')").fetchone()[0])

    def test_known_revision_without_catproof_tables_is_not_adopted(self):
        with self.connect() as db:
            db.execute('CREATE TABLE alembic_version(version_num VARCHAR(32) PRIMARY KEY)')
            db.execute("INSERT INTO alembic_version VALUES('h0004')")
        self.upgrade()
        with self.connect() as db:
            require_hosted_schema(db)
            self.assertEqual(db.execute('SELECT version_num FROM alembic_version').fetchall(),[('h0004',)])

    def test_unknown_old_catproof_revision_is_not_overwritten(self):
        self.upgrade('h0004')
        with self.connect() as db:
            db.execute('ALTER TABLE catproof_alembic_version RENAME TO alembic_version')
            db.execute("UPDATE alembic_version SET version_num='h9999'")
        with self.assertRaisesRegex(RuntimeError,'revision'):self.upgrade()
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT version_num FROM alembic_version').fetchall(),[('h9999',)])
            self.assertIsNone(db.execute("SELECT to_regclass('catproof_alembic_version')").fetchone()[0])

    def seed_observation(self,db):
        db.execute("INSERT INTO catproof_connections(tenant_id,id,name,kind,url,credential_prefix,source_filter,created_at) VALUES('legacy','c','Source','mcp','https://fixture.invalid','','',now())")
        db.execute("INSERT INTO catproof_entities(tenant_id,id,name,created_at,updated_at) VALUES('legacy','e','Entity',now(),now())")
        db.execute("INSERT INTO catproof_observations(tenant_id,id,connection_id,payload,first_seen) VALUES('legacy','o','c','{}',now())")

    def test_required_binding_is_deferred_and_enforced_on_every_write(self):
        self.upgrade()
        with self.assertRaises(psycopg.errors.ForeignKeyViolation):
            with self.connect() as db:self.seed_observation(db)
        with self.connect() as db:
            self.seed_observation(db)
            db.execute("INSERT INTO catproof_bindings VALUES('legacy','o','e')")
        with self.assertRaises(psycopg.errors.ForeignKeyViolation):
            with self.connect() as db:db.execute("DELETE FROM catproof_bindings WHERE tenant_id='legacy' AND observation_id='o'")
        # Pair deletion remains possible in the same transaction.
        with self.connect() as db:
            db.execute("DELETE FROM catproof_bindings WHERE tenant_id='legacy' AND observation_id='o'")
            db.execute("DELETE FROM catproof_observations WHERE tenant_id='legacy' AND id='o'")

    def test_existing_orphan_aborts_upgrade_without_changing_revision(self):
        self.upgrade('h0004')
        with self.connect() as db:self.seed_observation(db)
        with self.assertRaises(sa.exc.IntegrityError):self.upgrade()
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT version_num FROM catproof_alembic_version').fetchone()[0],'h0004')
            self.assertEqual(db.execute('SELECT id FROM catproof_observations').fetchall(),[('o',)])

    def test_failed_upgrade_rolls_back_legacy_version_table_adoption(self):
        self.upgrade('h0004')
        with self.connect() as db:
            self.seed_observation(db)
            db.execute('ALTER TABLE catproof_alembic_version RENAME TO alembic_version')
        with self.assertRaises(sa.exc.IntegrityError):self.upgrade()
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT version_num FROM alembic_version').fetchall(),[('h0004',)])
            self.assertIsNone(db.execute("SELECT to_regclass('catproof_alembic_version')").fetchone()[0])
            self.assertEqual(db.execute('SELECT id FROM catproof_observations').fetchall(),[('o',)])

    def test_latest_history_uses_index_under_inventory_rls(self):
        self.upgrade()
        with self.connect() as db:
            self.seed_observation(db)
            db.execute("INSERT INTO catproof_bindings VALUES('legacy','o','e')")
            db.execute("INSERT INTO catproof_sync_runs(tenant_id,id,connection_id,started_at,completed_at,outcome,observed) SELECT 'legacy',i::text,'c',now(),now()+i*interval '1 second','succeeded',0 FROM generate_series(1,20000) i")
            db.execute('ANALYZE catproof_sync_runs')
        store=FederatedRegistry(self.url,connect=self.connect,env={})
        try:
            result=store.db.execute('EXPLAIN (ANALYZE, FORMAT JSON) SELECT * FROM catproof_sync_runs ORDER BY completed_at DESC,sequence DESC LIMIT 100').fetchone()
            plan=json.loads(result['QUERY PLAN']) if isinstance(result['QUERY PLAN'],str) else result['QUERY PLAN']
            def nodes(node):
                yield node
                for child in node.get('Plans',[]):yield from nodes(child)
            parts=list(nodes(plan[0]['Plan']))
            self.assertTrue(any(p.get('Index Name')=='ix_sync_latest' for p in parts),parts)
            self.assertFalse(any(p['Node Type'] in ('Seq Scan','Sort') for p in parts),parts)
            self.assertEqual(parts[0]['Actual Rows'],100)
        finally:store.db.close()
