"""Community policy across models, transactions, authenticated APIs and upgrades."""
import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from federation import FederatedRegistry, ConnectionLimitError, DuplicateConnection
from hosted_app import create_server
from registry_locks import lock_registry
from tests.db import database
from test_federation import record, payload
import test_migrations as migration_fixtures
import test_enterprise as enterprise_fixtures


def connection(name, kind='a2a'):
    return {'name':name, 'kind':kind, 'url':'https://'+name+'.example.test'}


class CommunityConnectionTests(unittest.TestCase):
    def setUp(self):
        self.label=tempfile.TemporaryDirectory()
        self.url,self.connect=database(self.label.name)
        self.registry=FederatedRegistry(self.url,connect=self.connect,env={'CATPROOF_CONNECTION_LIMIT':'999'})

    def tearDown(self):
        self.registry.db.close();self.label.cleanup()

    def test_two_across_types_paused_counts_and_manual_records_do_not(self):
        first=self.registry.add_connection(connection('one','atlas'))
        self.registry.add_connection(connection('two','mcp'))
        self.registry.set_enabled(first,False)
        self.registry.create({'name':'Manual agent','business_owner':'Finance'})
        with self.assertRaises(ConnectionLimitError):self.registry.add_connection(connection('three'))
        limits=self.registry.snapshot()['connection_limits']
        self.assertEqual((limits['maximum'],limits['used'],limits['available']),(2,2,0))
        with self.assertRaises(DuplicateConnection):self.registry.add_connection(connection('one','atlas'))

    def test_archive_frees_slot_without_deleting_evidence_and_restore_is_limited(self):
        first=self.registry.add_connection(connection('one'))
        self.registry.add_connection(connection('two'))
        self.registry.ingest(first,payload(record()))
        agent=self.registry.snapshot()['agents'][0]
        self.registry.update(agent['registry_id'],{'business_owner':'Finance'})
        self.registry.set_archived(first,True)
        third=self.registry.add_connection(connection('three'))
        saved=self.registry.snapshot()
        self.assertEqual(saved['agents'][0]['business_owner'],'Finance')
        self.assertEqual(len(saved['observations']),1)
        self.assertTrue(saved['observations'][0]['stale'])
        self.assertEqual(next(s for s in saved['sources'] if s['id']==first)['health'],'archived')
        with self.assertRaises(ConnectionLimitError):self.registry.set_archived(first,False)
        with self.assertRaises(ValueError):self.registry.set_enabled(first,True)
        self.registry.set_archived(third,True)
        self.registry.set_archived(first,False)
        restored=next(s for s in self.registry.snapshot()['sources'] if s['id']==first)
        self.assertFalse(restored['enabled'])
        self.assertEqual(self.registry.snapshot()['connection_limits']['used'],2)

    def test_old_over_limit_inventory_is_readable_but_only_two_sources_are_called(self):
        old=FederatedRegistry(self.url,connect=self.connect,env={},connection_limit=None)
        try:
            ids=[old.add_connection(connection(name)) for name in ('old-one','old-two','old-three')]
        finally:old.db.close()
        called=[]
        self.registry.reader=lambda c,*args:(called.append(c['id']) or payload())
        available=self.registry.available_connection_ids();blocked=next(cid for cid in ids if cid not in available)
        self.registry.refresh()
        self.assertEqual(set(called),set(available))
        with self.assertRaises(ConnectionLimitError):self.registry.refresh(blocked)
        self.assertEqual(len(self.registry.snapshot()['sources']),3)
        self.assertTrue(self.registry.snapshot()['connection_limits']['over_limit'])
        self.registry.set_archived(available[0],True)
        called.clear();self.registry.refresh(blocked);self.assertEqual(called,[blocked])

    def test_count_is_serialized_across_database_connections(self):
        self.registry.add_connection(connection('first'))
        contender=FederatedRegistry(self.url,connect=self.connect,env={})
        executor=ThreadPoolExecutor(1)
        try:
            with self.connect() as held:
                lock_registry(held,'legacy')
                future=executor.submit(contender.add_connection,connection('contender'))
                deadline=time.monotonic()+4;waiting=False
                while time.monotonic()<deadline:
                    with self.connect() as observer:
                        waiting=bool(observer.execute("SELECT 1 FROM pg_locks WHERE pid=%s AND locktype='advisory' AND NOT granted",(contender.db.raw.info.backend_pid,)).fetchone())
                    if waiting:break
                    time.sleep(.01)
                self.assertTrue(waiting,'Concurrent creation must wait on the shared registry lock')
                winner=FederatedRegistry(db=held,workspace_id='legacy',env={})
                winner.add_connection(connection('winner'))
            with self.assertRaises(ConnectionLimitError):future.result(timeout=5)
            self.assertEqual(len(self.registry.snapshot()['sources']),2)
        finally:
            executor.shutdown(wait=True);contender.db.close()


class CommunityConnectionHTTPTests(unittest.TestCase):
    setUp=enterprise_fixtures.EnterpriseTests.setUp
    tearDown=enterprise_fixtures.EnterpriseTests.tearDown
    signup=enterprise_fixtures.EnterpriseTests.signup
    join=enterprise_fixtures.EnterpriseTests.join

    def request(self,path,body,token=None):
        req=Request(self.origin+path,data=json.dumps(body).encode(),headers={'Host':'registry.example','Origin':'https://registry.example','X-Registry-Action':'refresh','Content-Type':'application/json','Cookie':'catproof_session='+(token or self.at)})
        try:response=urlopen(req)
        except HTTPError as exc:response=exc
        with response:return response.code,json.load(response)

    def start(self):
        from cryptography.fernet import Fernet
        self.key=Fernet.generate_key().decode()
        self.server=create_server({'APP_PASSWORD':'fixture-bootstrap-password','APP_ORIGIN':'https://registry.example','CONNECTOR_ENCRYPTION_KEYS':self.key},self.store,('127.0.0.1',0),accounts=self.accounts)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.origin='http://127.0.0.1:'+str(self.server.server_port)
        self.addCleanup(self.server.server_close);self.addCleanup(self.thread.join);self.addCleanup(self.server.shutdown)

    def test_both_create_apis_enforce_limit_and_roles_cannot_archive(self):
        self.start()
        for name in ('one','two'):
            code,result=self.request('/api/connections',connection(name))
            self.assertEqual(code,201)
        first=result['sources'][0]['id']
        for path,body in [('/api/connections',connection('third')),('/api/credentials/connect',dict(connection('third'),credentials={'mode':'none'}))]:
            code,result=self.request(path,body)
            self.assertEqual(code,409)
            self.assertEqual(result['error_code'],'community_connection_limit')
        self.join('editor')
        self.assertEqual(self.request('/api/connection-archive',{'connection_id':first,'archived':True},self.bt)[0],403)
        self.assertEqual(self.request('/api/connection-archive',{'connection_id':first,'archived':True})[0],200)
        self.assertEqual(self.request('/api/credentials/connect',dict(connection('third'),credentials={'mode':'none'}))[0],200)
        self.assertEqual(self.request('/api/connection-archive',{'connection_id':first,'archived':False})[0],409)

    def test_registry_limits_are_isolated_and_over_limit_preview_never_fetches(self):
        self.start()
        for token in (self.at,self.bt):
            for name in ('one','two'):self.assertEqual(self.request('/api/connections',connection(name),token)[0],201)
        old=FederatedRegistry(self.url if hasattr(self,'url') else self.path,connect=self.connect,env={},workspace_id=self.a['workspace_id'],connection_limit=None)
        try:third=old.add_connection(connection('legacy-third'))
        finally:old.db.close()
        with self.store.request(False,self.a['workspace_id']) as registry:
            available=set(registry.available_connection_ids())
            blocked=next(s['id'] for s in registry.snapshot()['sources'] if s['id'] not in available)
        from unittest.mock import patch
        with patch('sync_worker.fetch_snapshot',side_effect=AssertionError('Must not contact a third source')) as fetch:
            self.assertEqual(self.request('/api/connection-preview',{'connection_id':blocked})[0],409)
            self.assertEqual(self.request('/api/refresh',{'connection_id':blocked})[0],409)
            fetch.assert_not_called()


class CommunityArchiveMigrationTests(unittest.TestCase):
    setUp=migration_fixtures.MigrationTests.setUp
    tearDown=migration_fixtures.MigrationTests.tearDown
    upgrade=migration_fixtures.MigrationTests.upgrade

    def test_upgrade_preserves_existing_connections(self):
        self.upgrade('h0007')
        with self.connect() as db:
            db.execute("INSERT INTO catproof_workspaces(id,name,created_at) VALUES('community-upgrade','Upgrade fixture',0)")
            for n in range(3):
                db.execute("INSERT INTO catproof_connections(tenant_id,id,name,kind,url,credential_prefix,source_filter,created_at) VALUES('community-upgrade',%s,%s,'a2a',%s,'','',CURRENT_TIMESTAMP)",(str(n),str(n),'https://fixture'+str(n)+'.example.test'))
        self.upgrade()
        with self.connect() as db:
            self.assertEqual(db.execute("SELECT count(*),sum(enabled),sum(archived) FROM catproof_connections WHERE tenant_id='community-upgrade'").fetchone(),(3,3,0))
