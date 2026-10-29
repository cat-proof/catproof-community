import contextlib
import tempfile
import unittest
from pathlib import Path
from accounts import Accounts,password_hash,password_matches
from hosted_store import HostedStore

from tests.db import connection as PostgreSQLConnection

class AccountsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=str(Path(self.tmp.name)/'accounts.db');self.connect=lambda *a,**kw:PostgreSQLConnection(self.path)
        self.store=HostedStore(self.path,self.connect);self.accounts=Accounts(self.path,self.connect)
    def tearDown(self):self.store.store.db.close();self.tmp.cleanup()
    def signup(self,email='owner@example.test'):
        return self.accounts.register({'name':'Fixture Owner','email':email,'workspace':'Fixture Workspace','password':'fixture-private-password'})
    def test_password_hashes_are_salted_and_checked(self):
        first=password_hash('fixture-password');second=password_hash('fixture-password')
        self.assertNotEqual(first,second);self.assertNotIn('fixture-password',first)
        self.assertTrue(password_matches('fixture-password',first));self.assertFalse(password_matches('incorrect',first))
    def test_signup_duplicate_is_atomic_and_sessions_are_revocable(self):
        user,token=self.signup();self.assertEqual(self.accounts.resolve(token)['workspace_id'],user['workspace_id'])
        self.assertFalse(user['email_verified']);self.assertNotIn('password_hash',user)
        with self.assertRaises(ValueError):self.signup('OWNER@example.test')
        with self.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM catproof_workspaces').fetchone()[0],2)
        self.assertIsNone(self.accounts.login('owner@example.test','wrong'))
        self.assertIsNotNone(self.accounts.login('OWNER@example.test','fixture-private-password'))
        self.accounts.revoke(token);self.assertIsNone(self.accounts.resolve(token))
    def test_cross_workspace_reads_writes_and_exports_are_isolated(self):
        a,_=self.signup('a@example.test');b,_=self.signup('b@example.test')
        with self.store.request(True,a['workspace_id']) as registry:
            eid=registry.create({'name':'Private A record'});cid=registry.add_connection({'name':'Private A source','kind':'a2a','url':'https://agent.example.test'})
        with self.store.request(False,b['workspace_id']) as registry:
            self.assertEqual(registry.snapshot()['agents'],[]);self.assertEqual(registry.snapshot()['sources'],[])
            self.assertNotIn(b'Private A',registry.export());self.assertNotIn('Private A',str(registry.catalog()))
            with self.assertRaises(KeyError):registry.update(eid,{'name':'Attack'})
            with self.assertRaises(ValueError):registry.refresh(cid)
        with self.store.request(False,a['workspace_id']) as registry:self.assertEqual(registry.snapshot()['agents'][0]['name'],'Private A record')
        with self.store.request(False,'legacy') as registry:self.assertEqual(registry.snapshot()['agents'],[])
    def test_tenant_cannot_inherit_other_tenant_or_legacy_credentials(self):
        from unittest.mock import patch
        user,_=self.signup();prefix='CATPROOF_'+user['workspace_id'].upper()+'_'
        with patch.dict('os.environ',{'SNOWFLAKE_TOKEN':'legacy-secret',prefix+'SNOWFLAKE_TOKEN':'own-secret','CATPROOF_OTHER_SNOWFLAKE_TOKEN':'other-secret'}):
            with self.store.request(False,user['workspace_id']) as registry:
                self.assertEqual(registry.env.get('SNOWFLAKE_TOKEN'),'own-secret')
                self.assertNotIn('legacy-secret',registry.env.values());self.assertNotIn('other-secret',registry.env.values())
    def test_password_change_invalidates_all_old_sessions(self):
        user,token=self.signup();second=self.accounts.login(user['email'],'fixture-private-password')[1]
        with self.assertRaises(ValueError):self.accounts.change_password(user['id'],'incorrect','another-long-password')
        current=self.accounts.change_password(user['id'],'fixture-private-password','another-long-password')
        self.assertIsNone(self.accounts.resolve(token));self.assertIsNone(self.accounts.resolve(second));self.assertIsNotNone(self.accounts.resolve(current))
        self.assertIsNone(self.accounts.login(user['email'],'fixture-private-password'))
    def test_forged_or_expired_sessions_and_untrusted_workspace_fail_closed(self):
        user,token=self.signup();self.assertIsNone(self.accounts.resolve('forged'))
        with self.connect() as db:db.execute('UPDATE catproof_sessions SET expires_at=0')
        self.assertIsNone(self.accounts.resolve(token))
        with self.assertRaises(RuntimeError):
            with self.store.request(False,'nonexistent'):pass
    def test_bad_signup_creates_nothing(self):
        for changes in ({'email':'bad'},{'password':'short'},{'workspace':''}):
            data={'name':'Owner','email':'owner@example.test','workspace':'Example','password':'fixture-private-password',**changes}
            with self.assertRaises(ValueError):self.accounts.register(data)
        with self.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM catproof_users').fetchone()[0],0)
    def test_restart_and_legacy_snapshot_migration_preserve_records(self):
        user,_=self.signup()
        with self.store.request(True,user['workspace_id']) as registry:registry.create({'name':'Persisted private record'})
        second=HostedStore(self.path,self.connect)
        with second.request(False,user['workspace_id']) as registry:self.assertEqual(registry.snapshot()['agents'][0]['name'],'Persisted private record')
        second.store.db.close()
