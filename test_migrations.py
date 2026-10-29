"""Real PostgreSQL initialization, legacy adoption, rollback and tenant integrity."""
import json
import os
import sqlite3
import time
import unittest
import uuid
from pathlib import Path
from urllib.parse import urlsplit
import psycopg
from psycopg import sql
from alembic import command
from database import configuration,engine_for,upgrade_hosted,HOSTED_HEAD,require_hosted_schema
from federation import FederatedRegistry

class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.url=os.getenv('TEST_POSTGRES_URL','')
        if not self.url:raise unittest.SkipTest('Requires disposable PostgreSQL')
        if urlsplit(self.url).path!='/catproof_test':raise RuntimeError('Only disposable test database allowed')
        self.schema='migration_'+uuid.uuid4().hex
        with psycopg.connect(self.url) as db:db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema)))
        self.connect=lambda *a,**kw:psycopg.connect(self.url,options='-c search_path='+self.schema,**kw)
        self.engine=engine_for(self.url,self.connect)
    def tearDown(self):
        self.engine.dispose()
        with psycopg.connect(self.url) as db:db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(self.schema)))
    def upgrade(self,target='head'):
        with self.engine.begin() as db:command.upgrade(configuration('hosted',db),target)
    def legacy_blob(self,bad=False):
        db=sqlite3.connect(':memory:');db.executescript((Path(__file__).parent/'tests/fixtures/legacy_inventory.sql').read_text())
        at='2026-10-27T00:00:00+00:00'
        db.execute("INSERT INTO connections(id,name,kind,url,credential_prefix,source_filter,created_at) VALUES('c','Fixture','mcp','https://fixture.invalid','','',?)",(at,))
        db.execute("INSERT INTO entities(id,name,created_at,updated_at,business_owner) VALUES('e','Keep me',?,?,'Owner')",(at,at))
        evidence=json.dumps({'id':'vendor','source':'Fixture','provenance':'live','name':'Observed','owner':'Source','status':'unknown','last_seen':at})
        db.execute('INSERT INTO observations VALUES(?,?,?,?,?,?)',('o','c',evidence,at,1,0))
        db.execute('INSERT INTO bindings VALUES(?,?)',('o','absent' if bad else 'e'))
        asset=json.dumps({'id':'tool@1','name':'Tool','source':'MCP','resource_type':'mcp_server','provenance':'live','last_seen':at,'evidence':{'version':'1'}})
        db.execute('INSERT INTO assets VALUES(?,?,?,?,?,?)',('a','c',asset,at,1,0));db.execute("INSERT INTO asset_links VALUES('a','e')")
        db.execute("INSERT INTO registry_events(sequence,entity_id,at,kind,name,detail,actor) VALUES(7,'e',?,'registered','Keep me','{}','owner@example.test')",(at,))
        db.execute("INSERT INTO sync_runs VALUES('r','c',?,?,'succeeded',2,NULL)",(at,at));db.commit()
        content=db.serialize();db.close();return content
    def seed_legacy(self,bad=False):
        self.upgrade('h0001');blob=self.legacy_blob(bad)
        with self.connect() as db:
            for tenant in ('one','two'):
                db.execute('INSERT INTO catproof_workspaces VALUES(%s,%s,%s)',(tenant,tenant,time.time()))
                db.execute('INSERT INTO catproof_users VALUES(%s,%s,%s,%s,%s,%s)',('u'+tenant,tenant+'@fixture.test','Owner',tenant,'fixture-password-hash',time.time()))
                db.execute("INSERT INTO catproof_members VALUES(%s,%s,'owner')",(tenant,'u'+tenant))
                db.execute('INSERT INTO catproof_member_initializations VALUES(%s)',('u'+tenant,))
                db.execute('INSERT INTO catproof_workspace_state(workspace_id,payload) VALUES(%s,%s)',(tenant,blob))
                db.execute("INSERT INTO catproof_connector_secrets VALUES(%s,'c','opaque-encrypted-fixture',%s)",(tenant,time.time()))
            # Represents the shipped layout before Alembic, not a stamped schema.
            db.execute('DROP TABLE catproof_alembic_version')
        return blob
    def test_fresh_init_repeat_upgrade_and_runtime_check(self):
        with self.connect() as db,self.assertRaisesRegex(RuntimeError,'not initialized'):require_hosted_schema(db)
        self.upgrade();self.upgrade()
        with self.connect() as db:
            require_hosted_schema(db)
            self.assertEqual(db.execute('SELECT version_num FROM catproof_alembic_version').fetchone()[0],HOSTED_HEAD)
            self.assertIsNone(db.execute("SELECT to_regclass('catproof_workspace_state')").fetchone()[0])
            db.execute("UPDATE catproof_alembic_version SET version_num='unknown_revision'")
        with self.connect() as db,self.assertRaisesRegex(RuntimeError,'revision'):require_hosted_schema(db)
    def test_unversioned_adoption_keeps_ids_links_audit_and_credentials(self):
        self.seed_legacy();self.upgrade();self.upgrade()
        for tenant in ('one','two'):
            store=FederatedRegistry(self.url,env={},connect=self.connect,workspace_id=tenant)
            try:
                result=store.snapshot();self.assertEqual(len(result['agents']),1)
                self.assertEqual(result['agents'][0]['id'],'e');self.assertEqual(result['agents'][0]['business_owner'],'Owner')
                self.assertEqual(result['assets'][0]['linked_agents'],['e']);self.assertEqual(result['events'][0]['sequence'],7)
                store.create({'name':'After migration'});self.assertGreater(store.snapshot()['events'][0]['sequence'],7)
            finally:store.db.close()
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM catproof_users').fetchone()[0],2)
            self.assertEqual(db.execute("SELECT count(*) FROM catproof_connector_secrets WHERE ciphertext='opaque-encrypted-fixture'").fetchone()[0],2)
    def test_large_legacy_scope_imports_before_unique_index_is_built(self):
        self.seed_legacy()
        with self.connect() as db:
            row=db.execute("SELECT payload FROM catproof_workspace_state WHERE workspace_id='one'").fetchone()
            old=sqlite3.connect(':memory:');old.deserialize(bytes(row[0]))
            scope=','.join(str(uuid.uuid4()) for _ in range(100))
            old.execute('UPDATE connections SET config=?',(json.dumps({'principal_ids':scope}),));old.commit()
            db.execute("UPDATE catproof_workspace_state SET payload=%s WHERE workspace_id='one'",(old.serialize(),));old.close()
        self.upgrade()
        with self.connect() as db:
            self.assertEqual(db.execute("SELECT config->>'principal_ids' FROM catproof_connections WHERE tenant_id='one'").fetchone()[0],scope)

    def test_existing_h0002_index_is_replaced_on_upgrade(self):
        self.upgrade('h0002')
        with self.connect() as db:
            db.execute('ALTER TABLE catproof_connections ADD UNIQUE(tenant_id,kind,url,source_filter,config)')
        self.upgrade()
        with self.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM pg_indexes WHERE schemaname=current_schema() AND indexname='uq_catproof_connection_scope'").fetchone()[0],1)
            self.assertEqual(db.execute("SELECT count(*) FROM pg_constraint WHERE conrelid='catproof_connections'::regclass AND contype='u'").fetchone()[0],0)

    def test_invalid_legacy_relation_rolls_back_without_dropping_blobs(self):
        blob=self.seed_legacy(bad=True)
        with self.assertRaisesRegex(RuntimeError,'import failed'):self.upgrade()
        with self.connect() as db:
            self.assertEqual(bytes(db.execute("SELECT payload FROM catproof_workspace_state WHERE workspace_id='one'").fetchone()[0]),blob)
            self.assertIsNone(db.execute("SELECT to_regclass('catproof_connections')").fetchone()[0])
    def test_row_security_and_composite_foreign_keys(self):
        self.upgrade()
        with self.connect() as db:
            db.execute("INSERT INTO catproof_workspaces VALUES('other','Other',0)")
        first=FederatedRegistry(self.url,env={},connect=self.connect)
        other=FederatedRegistry(self.url,env={},connect=self.connect,workspace_id='other')
        try:
            eid=first.create({'name':'Private'});cid=other.add_connection({'name':'Other','kind':'mcp','url':'https://other.invalid'})
            self.assertEqual(other.snapshot()['agents'],[])
            self.assertEqual(other.db.execute('SELECT * FROM catproof_entities WHERE id=?',(eid,)).fetchall(),[])
            with self.assertRaises(psycopg.errors.InsufficientPrivilege),other.db:
                other.db.execute("INSERT INTO catproof_entities(tenant_id,id,name,created_at,updated_at) VALUES('legacy','attack','Attack',now(),now())")
            with self.assertRaises(psycopg.errors.ForeignKeyViolation),other.db:
                other.db.execute("INSERT INTO catproof_asset_links(asset_id,entity_id) VALUES('missing',?)",(eid,))
            with self.connect() as db,self.assertRaises(psycopg.errors.ForeignKeyViolation):
                db.execute("INSERT INTO catproof_connector_secrets VALUES('legacy',%s,'cipher',0)",(cid,))
        finally:first.db.close();other.db.close()
    def test_two_processes_can_initialize_same_database(self):
        import subprocess,sys
        env={**os.environ,'DATABASE_URL':self.url,'PGOPTIONS':'-c search_path='+self.schema}
        commands=[subprocess.Popen([sys.executable,'database.py','upgrade'],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE) for _ in range(2)]
        for process in commands:
            out,err=process.communicate(timeout=30);self.assertEqual(process.returncode,0,err.decode())
        with self.connect() as db:require_hosted_schema(db)

    def test_recovery_table_adoption_preserves_consumed_and_pending_grants(self):
        self.upgrade('h0003')
        with self.connect() as db:
            db.execute("INSERT INTO catproof_users VALUES('owner','owner@fixture.test','Owner','legacy','fixture-hash',0)")
            db.execute('CREATE TABLE catproof_account_recovery (token_hash TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES catproof_users(id),expires_at DOUBLE PRECISION NOT NULL,used_at DOUBLE PRECISION)')
            db.execute("INSERT INTO catproof_account_recovery VALUES('used','owner',100,90),('pending','owner',200,NULL)")
        self.upgrade();self.upgrade()
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT token_hash,expires_at,used_at FROM catproof_account_recovery ORDER BY token_hash').fetchall(),[('pending',200,None),('used',100,90)])
            self.assertEqual(db.execute("SELECT count(*) FROM pg_indexes WHERE schemaname=current_schema() AND tablename='catproof_account_recovery'").fetchone()[0],3)
