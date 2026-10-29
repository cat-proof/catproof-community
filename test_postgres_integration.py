"""Same security semantics against an isolated real PostgreSQL schema in CI."""
import os
import unittest
import uuid
import test_enterprise
from enterprise import RegistryAccounts
from hosted_store import HostedStore

@unittest.skipUnless(os.getenv('TEST_POSTGRES_URL'),'Set TEST_POSTGRES_URL to the disposable CI database')
class PostgresIntegrationTests(test_enterprise.EnterpriseTests):
    def setUp(self):
        import psycopg
        from psycopg import sql
        from urllib.parse import urlsplit
        self.path=os.environ['TEST_POSTGRES_URL']
        if urlsplit(self.path).path!='/catproof_test':raise RuntimeError('Integration tests require the disposable catproof_test database')
        self.schema='test_'+uuid.uuid4().hex
        with psycopg.connect(self.path) as db:db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema)))
        self.connect=lambda *a,**kw:psycopg.connect(self.path,options='-c search_path='+self.schema,**kw)
        from database import upgrade_hosted
        upgrade_hosted(self.path,creator=lambda:self.connect())
        self.store=HostedStore(self.path,self.connect);self.accounts=RegistryAccounts(self.path,self.connect)
        self.a,self.at=self.signup('owner@example.test');self.b,self.bt=self.signup('teammate@example.test')
    def tearDown(self):
        import psycopg
        from psycopg import sql
        self.store.store.db.close()
        with psycopg.connect(self.path) as db:db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(self.schema)))

import test_local_devices
from local_devices import LocalDevices

@unittest.skipUnless(os.getenv('TEST_POSTGRES_URL'),'Set TEST_POSTGRES_URL to the disposable CI database')
class PostgresLocalDeviceTests(test_local_devices.LocalDeviceTests):
    def setUp(self):
        PostgresIntegrationTests.setUp(self)
        self.devices=LocalDevices(self.accounts)
    tearDown=PostgresIntegrationTests.tearDown

import test_connector_secrets

@unittest.skipUnless(os.getenv('TEST_POSTGRES_URL'),'Set TEST_POSTGRES_URL to the disposable CI database')
class PostgresSecretTests(test_connector_secrets.SecretTests):
    def setUp(self):
        PostgresIntegrationTests.setUp(self)
        self.initialize_secrets()
    tearDown=PostgresIntegrationTests.tearDown

    def test_credential_write_serializes_with_registry_snapshot_write(self):
        import threading
        cid=self.save();entered=threading.Event();done=threading.Event();errors=[]
        def save_key():
            entered.set()
            try:self.secrets.change(self.a,{'connection_id':cid,'api_key':'fixture-concurrent-key'})
            except Exception as exc:errors.append(exc)
            finally:done.set()
        with self.store.request(True,self.a['workspace_id']) as store:
            store.create({'name':'Concurrent canonical record'})
            thread=threading.Thread(target=save_key);thread.start()
            self.assertTrue(entered.wait(2))
            self.assertFalse(done.wait(.2),'Credential write bypassed registry lock')
        thread.join(6)
        self.assertTrue(done.is_set());self.assertEqual(errors,[])
        with self.store.request(False,self.a['workspace_id']) as store:
            self.assertEqual(store.credentials[cid],'fixture-concurrent-key')
            self.assertEqual(store.snapshot()['agents'][0]['name'],'Concurrent canonical record')

import test_hosted_sync
import test_peer_tokens

@unittest.skipUnless(os.getenv('TEST_POSTGRES_URL'),'Set TEST_POSTGRES_URL to the disposable CI database')
class PostgresSyncTests(test_hosted_sync.HostedSyncTests):
    setUp=PostgresIntegrationTests.setUp
    tearDown=PostgresIntegrationTests.tearDown

    def test_database_locks_serialize_same_registry_without_blocking_another(self):
        import threading
        entered=threading.Event();release=threading.Event();same_done=threading.Event();other_done=threading.Event()
        errors=[]
        def first():
            try:
                with self.store.request(True,self.a['workspace_id']) as registry:
                    registry.create({'name':'First write'});entered.set()
                    if not release.wait(4):raise RuntimeError('Test lock was not released')
            except Exception as exc:errors.append(exc)
        def writer(workspace,name,done):
            try:
                with self.store.request(True,workspace) as registry:registry.create({'name':name})
                done.set()
            except Exception as exc:errors.append(exc)
        one=threading.Thread(target=first);one.start()
        self.assertTrue(entered.wait(3))
        same=threading.Thread(target=writer,args=(self.a['workspace_id'],'Second write',same_done))
        other=threading.Thread(target=writer,args=(self.b['workspace_id'],'Independent write',other_done))
        same.start();other.start()
        try:
            self.assertTrue(other_done.wait(2),'A different registry shared the database lock')
            self.assertFalse(same_done.is_set(),'Same-registry writes were not serialized')
        finally:
            release.set()
            for thread in (one,same,other):thread.join(6)
        self.assertEqual(errors,[])
        self.assertTrue(same_done.is_set())
        with self.store.request(False,self.a['workspace_id']) as registry:
            self.assertEqual({a['name'] for a in registry.snapshot()['agents']},{'First write','Second write'})

@unittest.skipUnless(os.getenv('TEST_POSTGRES_URL'),'Set TEST_POSTGRES_URL to the disposable CI database')
class PostgresPeerTokenTests(test_peer_tokens.PeerTokenTests):
    setUp=PostgresIntegrationTests.setUp
    tearDown=PostgresIntegrationTests.tearDown

import test_peer_http

@unittest.skipUnless(os.getenv('TEST_POSTGRES_URL'),'Set TEST_POSTGRES_URL to the disposable CI database')
class PostgresPeerHTTPTests(test_peer_http.PeerHTTPTests):
    def setUp(self):
        PostgresIntegrationTests.setUp(self)
        self.start_http()
    def tearDown(self):
        self.stop_http()
        PostgresIntegrationTests.tearDown(self)
