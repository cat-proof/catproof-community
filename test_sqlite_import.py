"""Offline standalone SQLite adoption exercised against real PostgreSQL."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

import sqlalchemy as sa
from psycopg import sql
from database import HOSTED_HEAD, upgrade_hosted
from legacy_import import ImportRefused
from retired_sources import RETIRED_IDS
from tests.db import database
from sqlite_import import ALL_TABLES, import_sqlite
from federation import FederatedRegistry
from product import ProductStore
import test_migrations as fixtures

AT='2026-10-27T00:00:00+00:00'

class SQLiteImportTests(unittest.TestCase):
    def setUp(self):
        self.url,self.connect=database()
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.path=Path(self.folder.name)/'sqlite.db'
    def source(self):
        self.path.write_bytes(fixtures.MigrationTests().legacy_blob())
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO entities(id,name,business_owner,purpose,lifecycle,review,notes,manual,archived,created_at,updated_at) VALUES('manual','Manual','Team','Purpose','retired','reviewed','Keep notes',1,1,?,?)",(AT,AT))
            db.execute('UPDATE observations SET present=0,stale=1')
            db.execute("INSERT INTO migrations VALUES('old-annotation-import',?)",(AT,))
        return self.path
    def run_import(self,tenant='legacy'):
        return import_sqlite(self.path,tenant,self.url,self.connect)
    def assert_empty(self):
        with self.connect() as db:
            for name in ALL_TABLES:
                self.assertEqual(db.execute('SELECT count(*) FROM catproof_'+name).fetchone()[0],0)
    def test_retired_id_is_refused_after_cleanup_and_on_cli_retry(self):
        self.source();retired_id=next(iter(RETIRED_IDS))
        with sqlite3.connect(self.path) as old:
            old.execute('UPDATE connections SET id=? WHERE id=\'c\'',(retired_id,))
            for table in ('observations','assets','sync_runs','registry_events'):
                old.execute('UPDATE '+table+' SET connection_id=? WHERE connection_id=\'c\'',(retired_id,))
            old.execute("INSERT INTO connections(id,name,kind,url,credential_prefix,source_filter,created_at) VALUES('good','Keep','mcp','https://good.invalid','','',?)",(AT,))
        before=self.path.read_bytes();self.path.chmod(0o400)
        with self.connect() as db:
            schema=db.execute('SELECT current_schema()').fetchone()[0]
            self.assertEqual(db.execute('SELECT version_num FROM catproof_alembic_version').fetchone()[0],HOSTED_HEAD)
            receipt=db.execute("SELECT receipt FROM catproof_data_migrations WHERE id='retired-source-postgres-v1'").fetchone()[0]
        with self.assertRaisesRegex(ImportRefused,'retired connections'):self.run_import()
        self.assert_empty();self.assertEqual(self.path.read_bytes(),before)
        upgrade_hosted(self.url,self.connect)
        env={**os.environ,'DATABASE_URL':self.url,'PGOPTIONS':'-c search_path='+schema}
        result=subprocess.run([sys.executable,'database.py','import-sqlite',str(self.path),'--registry-id','legacy'],env=env,capture_output=True,text=True,timeout=30)
        self.assertNotEqual(result.returncode,0);self.assertIn('retired connections',result.stderr)
        self.assertNotIn(retired_id,result.stdout+result.stderr)
        self.assertNotIn(self.url,result.stdout+result.stderr)
        self.assert_empty();self.assertEqual(self.path.read_bytes(),before)
        with self.connect() as db:
            self.assertEqual(db.execute("SELECT receipt FROM catproof_data_migrations WHERE id='retired-source-postgres-v1'").fetchone()[0],receipt)
    def test_retired_origin_with_unrelated_id_is_refused_even_when_paused(self):
        self.source()
        origin='https://Retired-Fixture.Invalid:8443/catalog?token=private-source-canary'
        with sqlite3.connect(self.path) as old:
            old.execute('UPDATE connections SET url=?,enabled=0 WHERE id=\'c\'',(origin,))
        before=self.path.read_bytes()
        fingerprint=hashlib.sha256(b'retired-fixture.invalid').hexdigest()
        with patch('retired_sources.RETIRED_HOSTS',frozenset({fingerprint})):
            with self.assertRaisesRegex(ImportRefused,'retired connections') as error:self.run_import()
        self.assertNotIn(origin,str(error.exception));self.assertNotIn('private-source-canary',str(error.exception))
        self.assert_empty();self.assertEqual(self.path.read_bytes(),before)
    def test_federated_import_preserves_data_and_source_then_retries_without_overwriting(self):
        self.source();before=self.path.read_bytes();self.path.chmod(0o400)
        result=self.run_import();self.assertEqual(result['counts']['entities'],2)
        self.assertEqual(self.path.read_bytes(),before)
        store=FederatedRegistry(self.url,connect=self.connect,env={});self.addCleanup(store.db.close)
        with self.connect() as db:
            row=db.execute("SELECT business_owner,purpose,lifecycle,review,notes,manual,archived FROM catproof_entities WHERE id='manual'").fetchone()
            self.assertEqual(row,('Team','Purpose','retired','reviewed','Keep notes',1,1))
            self.assertEqual(db.execute('SELECT id,present,stale FROM catproof_observations').fetchone(),('o',0,1))
            self.assertEqual(db.execute('SELECT asset_id,entity_id FROM catproof_asset_links').fetchone(),('a','e'))
            self.assertEqual(db.execute('SELECT id,outcome FROM catproof_sync_runs').fetchone(),('r','succeeded'))
            self.assertEqual(db.execute('SELECT sequence,actor FROM catproof_registry_events').fetchone(),(7,'owner@example.test'))
        store.create({'name':'New after import'})
        self.assertEqual(self.run_import()['status'],'already_imported')
        self.assertIn('New after import',[agent['name'] for agent in store.snapshot()['agents']])
        with self.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM catproof_entities').fetchone()[0],3)
        self.assertGreater(store.snapshot()['events'][0]['sequence'],7)
        self.assertEqual(self.path.read_bytes(),before)
    def test_product_import_preserves_namespaces_annotations_history_and_sequence(self):
        with sqlite3.connect(self.path) as old:
            old.executescript(Path('tests/fixtures/legacy_product.sql').read_text())
            for namespace in ('one','two'):
                payload={'id':namespace,'name':namespace,'source':'Fixture','provenance':'seeded','last_seen':AT,'status':'active','owner':'Source'}
                old.execute('INSERT INTO agents VALUES(?,?,?,?,?,?,?,?,?,?)',(namespace,namespace,namespace,json.dumps(payload),AT,0,'Team','reviewed','Notes',AT))
                old.execute('INSERT INTO metadata VALUES(?,?,?)',(namespace,'[]',AT))
            old.execute("INSERT INTO events VALUES(9000,'one','one',?,'registered','one','{}')",(AT,))
        with self.connect() as db:db.execute("SELECT setval(pg_get_serial_sequence('catproof_events','sequence'),12000,true)")
        before=self.path.read_bytes();result=self.run_import();self.assertEqual(result['format'],'product')
        store=ProductStore(self.url,'one',connect=self.connect);self.addCleanup(store.db.close)
        snapshot=store.snapshot();self.assertEqual(len(snapshot['agents']),1)
        self.assertEqual(snapshot['agents'][0]['registry_id'],'one');self.assertEqual(snapshot['agents'][0]['notes'],'Notes')
        self.assertFalse(snapshot['agents'][0]['present']);self.assertEqual(snapshot['events'][0]['sequence'],9000)
        store.annotate('one',{'notes':'Changed'})
        self.assertGreater(store.snapshot()['events'][0]['sequence'],12000)
        self.assertEqual(self.path.read_bytes(),before)
    def test_explicit_tenant_isolated_and_unknown_or_occupied_target_refused(self):
        self.source()
        with self.assertRaisesRegex(ValueError,'does not exist'):self.run_import('missing')
        with self.connect() as db:db.execute("INSERT INTO catproof_workspaces VALUES('other','Other',0)")
        self.run_import('other')
        with self.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM catproof_entities WHERE tenant_id='legacy'").fetchone()[0],0)
        store=FederatedRegistry(self.url,connect=self.connect,env={});self.addCleanup(store.db.close)
        store.create({'name':'Existing'})
        with self.assertRaisesRegex(ValueError,'already contains'):self.run_import()
        self.assertEqual(store.snapshot()['agents'][0]['name'],'Existing')
        with sqlite3.connect(self.path) as old:old.execute("UPDATE entities SET name='Changed source' WHERE id='e'")
        with self.assertRaisesRegex(ValueError,'already contains'):self.run_import('other')
    def test_database_constraint_failure_rolls_back_every_imported_row(self):
        self.source()
        with sqlite3.connect(self.path) as old:old.execute("UPDATE entities SET lifecycle='invalid' WHERE id='manual'")
        with self.assertRaises(sa.exc.IntegrityError):self.run_import()
        self.assert_empty()
    def test_corrupt_missing_journal_and_unknown_schema_are_rejected(self):
        with self.assertRaises(FileNotFoundError):self.run_import()
        self.assertFalse(self.path.exists())
        self.path.write_bytes(b'not a database')
        with self.assertRaises(sqlite3.DatabaseError):self.run_import()
        self.source();sidecar=Path(str(self.path)+'-wal');sidecar.write_bytes(b'active')
        with self.assertRaisesRegex(ValueError,'sidecars'):self.run_import()
        sidecar.unlink()
        with sqlite3.connect(self.path) as old:old.execute('CREATE TABLE unsupported(secret TEXT)')
        with self.assertRaisesRegex(ValueError,'Unsupported or mixed'):self.run_import()
        self.assert_empty()
    def test_unknown_columns_are_rejected_even_in_empty_tables(self):
        self.source()
        with sqlite3.connect(self.path) as old:
            old.execute('DELETE FROM sync_runs');old.execute('ALTER TABLE sync_runs ADD COLUMN future_field TEXT')
        with self.assertRaisesRegex(ValueError,'Unsupported columns'):self.run_import()
        self.assert_empty()
    def test_older_schema_defaults_and_wal_checkpointed_backup(self):
        self.source()
        with sqlite3.connect(self.path) as old:
            old.execute('ALTER TABLE connections DROP COLUMN sync_generation')
            old.execute('ALTER TABLE connections DROP COLUMN config')
            old.execute('ALTER TABLE registry_events DROP COLUMN actor')
        old=sqlite3.connect(self.path);old.execute('PRAGMA journal_mode=WAL');old.execute('PRAGMA wal_checkpoint(TRUNCATE)');old.close()
        self.run_import()
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT config,sync_generation FROM catproof_connections').fetchone(),({},''))
            self.assertEqual(db.execute('SELECT actor FROM catproof_registry_events').fetchone()[0],'')
    def test_cli_concurrent_retry_and_safe_failure_output(self):
        self.source()
        with self.connect() as db:schema=db.execute('SELECT current_schema()').fetchone()[0]
        env={**os.environ,'DATABASE_URL':self.url,'PGOPTIONS':'-c search_path='+schema}
        cmd=[sys.executable,'database.py','import-sqlite',str(self.path),'--registry-id','legacy']
        processes=[subprocess.Popen(cmd,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for _ in range(2)]
        statuses=[]
        for process in processes:
            out,err=process.communicate(timeout=30);self.assertEqual(process.returncode,0,err);statuses.append(json.loads(out)['status'])
        self.assertEqual(sorted(statuses),['already_imported','imported'])
        with sqlite3.connect(self.path) as old:old.execute("UPDATE entities SET notes='secret-canary' WHERE id='e'")
        result=subprocess.run(cmd,env=env,capture_output=True,text=True,timeout=30)
        self.assertNotEqual(result.returncode,0);self.assertNotIn('secret-canary',result.stdout+result.stderr)
        self.assertNotIn(self.url,result.stdout+result.stderr)

    def test_cli_conversion_error_does_not_echo_source_values(self):
        self.source()
        with sqlite3.connect(self.path) as old:old.execute("UPDATE entities SET created_at='private-source-canary'")
        with self.connect() as db:schema=db.execute('SELECT current_schema()').fetchone()[0]
        env={**os.environ,'DATABASE_URL':self.url,'PGOPTIONS':'-c search_path='+schema}
        result=subprocess.run([sys.executable,'database.py','import-sqlite',str(self.path),'--registry-id','legacy'],env=env,capture_output=True,text=True,timeout=30)
        self.assertNotEqual(result.returncode,0)
        self.assertNotIn('private-source-canary',result.stdout+result.stderr)
        self.assertNotIn(self.url,result.stdout+result.stderr);self.assert_empty()

    def test_import_with_non_superuser_operator_obeys_tenant_policy(self):
        self.source();role='import_'+uuid.uuid4().hex
        with self.connect() as db:
            schema=db.execute('SELECT current_schema()').fetchone()[0]
            db.execute(sql.SQL('CREATE ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS').format(sql.Identifier(role)))
            db.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(sql.Identifier(schema),sql.Identifier(role)))
            db.execute(sql.SQL('GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA {} TO {}').format(sql.Identifier(schema),sql.Identifier(role)))
            db.execute(sql.SQL('GRANT SELECT,USAGE,UPDATE ON ALL SEQUENCES IN SCHEMA {} TO {}').format(sql.Identifier(schema),sql.Identifier(role)))
        try:
            def operator_connect():
                db=self.connect();db.execute(sql.SQL('SET ROLE {}').format(sql.Identifier(role)));db.commit();return db
            self.assertEqual(import_sqlite(self.path,'legacy',self.url,operator_connect)['status'],'imported')
        finally:
            with self.connect() as db:
                db.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)))
                db.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))
