import unittest
from tests.db import registry
import test_migrations as fixtures
from unittest.mock import patch
from database import HOSTED_HEAD
from hosted_store import HostedStore
from retired_sources import RETIRED_IDS
from test_federation import record,payload
from retired_sources import purge_retired

class RetiredSourceTests(unittest.TestCase):
    def test_removes_only_retired_evidence_and_preserves_manual_and_shared_records(self):
        s=registry(env={})
        try:
            old=s.add_connection({'name':'Retired fixture','kind':'atlas','url':'https://retired.example.test'})
            good=s.add_connection({'name':'Authorized fixture','kind':'atlas','url':'https://authorized.example.test'})
            s.ingest(old,payload(record('Source only',rid='one'),record('Shared',rid='two')))
            s.ingest(good,payload(record('Independent',rid='three')))
            snap=s.snapshot();entities={r['name']:r['registry_id'] for r in snap['agents']}
            independent=next(o for o in snap['observations'] if o['connection_id']==good)
            s.link(independent['observation_id'],entities['Shared'])
            with s.db:result=purge_retired(s.db,{old},lambda _:False)
            self.assertEqual(result,{'connections':[old],'entities_removed':1,'credentials_removed':0})
            self.assertIsNotNone(s.db.execute('SELECT id FROM catproof_entities WHERE id=?',(entities['Shared'],)).fetchone())
            self.assertEqual(s.db.execute('SELECT count(*) FROM catproof_observations').fetchone()[0],1)
            self.assertEqual([r[0] for r in s.db.execute('SELECT id FROM catproof_connections')],[good])
            with s.db:s.db.execute('SET CONSTRAINTS ALL IMMEDIATE')
            with s.db:self.assertEqual(purge_retired(s.db,{old},lambda _:False)['connections'],[])
        finally:s.db.close()

    def test_manual_record_survives_removal_of_its_only_source(self):
        s=registry(env={})
        try:
            cid=s.add_connection({'name':'Retired fixture','kind':'atlas','url':'https://retired.example.test'})
            s.ingest(cid,payload(record('Observed')))
            eid=s.snapshot()['agents'][0]['registry_id']
            with s.db:s.db.execute("UPDATE catproof_entities SET manual=1,business_owner='Finance' WHERE id=?",(eid,))
            with s.db:purge_retired(s.db,{cid},lambda _:False)
            self.assertEqual(s.db.execute('SELECT business_owner FROM catproof_entities WHERE id=?',(eid,)).fetchone()[0],'Finance')
        finally:s.db.close()

class HostedRetirementTests(unittest.TestCase):
    setUp=fixtures.MigrationTests.setUp
    tearDown=fixtures.MigrationTests.tearDown
    upgrade=fixtures.MigrationTests.upgrade

    def seed(self):
        self.upgrade('h0005')
        cid=next(iter(RETIRED_IDS))
        with self.connect() as db:
            db.execute("INSERT INTO catproof_workspaces VALUES('second','Second',0)")
            # Adopt and preserve the receipt written by main's older cleanup.
            db.execute('CREATE TABLE catproof_data_migrations(id TEXT PRIMARY KEY,receipt TEXT NOT NULL)')
            db.execute("INSERT INTO catproof_data_migrations VALUES('retired-source-v1','{}')")
            for tenant in ('legacy','second'):
                for connection in (cid,'good'):
                    db.execute("INSERT INTO catproof_connections(tenant_id,id,name,kind,url,credential_prefix,source_filter,created_at) VALUES(%s,%s,'Fixture','atlas',%s,'','',now())",(tenant,connection,'https://'+connection+'.fixture.invalid'))
                    db.execute('INSERT INTO catproof_connector_secrets VALUES(%s,%s,%s,0)',(tenant,connection,'synthetic ciphertext'))
                db.execute("INSERT INTO catproof_entities(tenant_id,id,name,created_at,updated_at) VALUES(%s,'entity','Fixture',now(),now())",(tenant,))
                db.execute("INSERT INTO catproof_observations(tenant_id,id,connection_id,payload,first_seen) VALUES(%s,'observation',%s,'{}',now())",(tenant,cid))
                db.execute("INSERT INTO catproof_bindings VALUES(%s,'observation','entity')",(tenant,))
                db.execute("INSERT INTO catproof_assets(tenant_id,id,connection_id,payload,first_seen) VALUES(%s,'asset',%s,'{}',now())",(tenant,cid))
                db.execute("INSERT INTO catproof_asset_links VALUES(%s,'asset','entity')",(tenant,))
                db.execute("INSERT INTO catproof_registry_events(tenant_id,entity_id,connection_id,at,kind,name,detail) VALUES(%s,'entity',%s,now(),'registered','Fixture','{}')",(tenant,cid))
                db.execute("INSERT INTO catproof_sync_runs(tenant_id,id,connection_id,started_at,completed_at,outcome,observed) VALUES(%s,'run',%s,now(),now(),'succeeded',1)",(tenant,cid))

    def test_all_registries_credentials_and_idempotent_receipt(self):
        self.seed();self.upgrade();self.upgrade()
        store=HostedStore(self.url,self.connect)
        try:
            expected={'registries_checked':2,'connections_removed':2,'entities_removed':2,'credentials_removed':2}
            self.assertEqual(store.remove_retired_sources(),expected)
            self.assertEqual(store.remove_retired_sources(),expected)
            with self.connect() as db:
                self.assertEqual(db.execute('SELECT version_num FROM catproof_alembic_version').fetchone()[0],HOSTED_HEAD)
                self.assertEqual(db.execute('SELECT tenant_id,id FROM catproof_connections ORDER BY tenant_id').fetchall(),[('legacy','good'),('second','good')])
                self.assertEqual(db.execute('SELECT registry_id,connection_id FROM catproof_connector_secrets ORDER BY registry_id').fetchall(),[('legacy','good'),('second','good')])
                for table in ('entities','observations','bindings','assets','asset_links','sync_runs','registry_events'):
                    self.assertEqual(db.execute('SELECT count(*) FROM catproof_'+table).fetchone()[0],0,table)
                self.assertEqual(db.execute("SELECT receipt FROM catproof_data_migrations WHERE id='retired-source-v1'").fetchone()[0],'{}')
                self.assertIsNone(db.execute("SELECT to_regclass('catproof_workspace_state')").fetchone()[0])
        finally:store.store.db.close()

    def test_failure_rolls_back_all_registries_credentials_and_receipt(self):
        self.seed();calls=[]
        def fail_second(db):
            calls.append(1)
            if len(calls)==2:raise RuntimeError('synthetic cleanup failure')
            return purge_retired(db)
        with patch('retired_sources.purge_retired',side_effect=fail_second):
            with self.assertRaisesRegex(RuntimeError,'synthetic cleanup failure'):self.upgrade()
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT version_num FROM catproof_alembic_version').fetchone()[0],'h0005')
            self.assertEqual(db.execute('SELECT count(*) FROM catproof_connections').fetchone()[0],4)
            self.assertEqual(db.execute('SELECT count(*) FROM catproof_connector_secrets').fetchone()[0],4)
            self.assertEqual(db.execute('SELECT count(*) FROM catproof_observations').fetchone()[0],2)
            self.assertEqual(db.execute('SELECT count(*) FROM catproof_data_migrations').fetchone()[0],1)
        self.upgrade()
