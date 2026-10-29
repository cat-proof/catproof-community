import tempfile
import time
import unittest
from pathlib import Path
from enterprise import RegistryAccounts,can_write,digest
from hosted_store import HostedStore
from test_accounts import PostgreSQLConnection

class EnterpriseTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=str(Path(self.tmp.name)/'db');self.connect=lambda *a,**kw:PostgreSQLConnection(self.path)
        self.store=HostedStore(self.path,self.connect);self.accounts=RegistryAccounts(self.path,self.connect)
        self.a,self.at=self.signup('owner@example.test');self.b,self.bt=self.signup('teammate@example.test')
    def tearDown(self):self.store.store.db.close();self.tmp.cleanup()
    def signup(self,email):
        user,token=self.accounts.register({'name':'Test Person','email':email,'workspace':'Test Registry','password':'fixture-personal-password'})
        return self.accounts.resolve(token),token
    def join(self,role='viewer'):
        invitation=self.accounts.invite(self.a,self.b['email'],role,'fixture-personal-password');self.accounts.accept(self.b,invitation['code'],self.bt)
        return self.accounts.resolve(self.bt),invitation
    def test_permission_matrix_denies_unknown_actions(self):
        self.assertTrue(can_write('owner','/api/connections'));self.assertFalse(can_write('editor','/api/connections'))
        self.assertTrue(can_write('editor','/api/annotate'));self.assertFalse(can_write('viewer','/api/annotate'))
        for role in ('owner','editor','viewer',None):self.assertFalse(can_write(role,'/api/unknown'))
    def test_email_bound_single_use_invites_and_scoped_switch(self):
        invitation=self.accounts.invite(self.a,self.b['email'],'viewer','fixture-personal-password')
        with self.assertRaises(ValueError):self.accounts.accept(self.a,invitation['code'],self.at)
        self.accounts.accept(self.b,invitation['code'],self.bt);joined=self.accounts.resolve(self.bt)
        self.assertEqual(joined['role'],'viewer');self.assertEqual(joined['workspace_id'],self.a['workspace_id'])
        with self.assertRaises(ValueError):self.accounts.accept(self.b,invitation['code'],self.bt)
        with self.assertRaises(PermissionError):self.accounts.switch(self.b,'foreign-registry',self.bt)
        self.accounts.switch(joined,self.b['workspace_id'],self.bt);self.assertEqual(self.accounts.resolve(self.bt)['role'],'owner')
        with self.connect() as db:
            encoded=db.execute('SELECT token_hash FROM catproof_invites').fetchone()[0];self.assertNotEqual(encoded,invitation['code'])
    def test_expired_revoked_and_replaced_invites_fail(self):
        first=self.accounts.invite(self.a,self.b['email'],'editor','fixture-personal-password')
        second=self.accounts.invite(self.a,self.b['email'],'viewer','fixture-personal-password')
        with self.assertRaises(ValueError):self.accounts.accept(self.b,first['code'],self.bt)
        self.accounts.cancel_invite(self.a,second['id'],'fixture-personal-password')
        with self.assertRaises(ValueError):self.accounts.accept(self.b,second['code'],self.bt)
        third=self.accounts.invite(self.a,self.b['email'],'viewer','fixture-personal-password')
        with self.connect() as db:db.execute('UPDATE catproof_invites SET expires_at=0')
        with self.assertRaises(ValueError):self.accounts.accept(self.b,third['code'],self.bt)
    def test_fresh_owner_authorization_and_password_confirmation(self):
        joined,_=self.join()
        with self.assertRaises(PermissionError):self.accounts.invite(joined,'other@example.test','viewer','fixture-personal-password')
        with self.assertRaises(PermissionError):self.accounts.invite(self.a,'other@example.test','viewer','wrong-password')
        with self.assertRaises(ValueError):self.accounts.member_change(self.a,self.a['id'],'viewer','fixture-personal-password')
        with self.assertRaises(ValueError):self.accounts.invite(self.a,'other@example.test','owner','fixture-personal-password')
    def test_roles_change_immediately_and_removal_survives_restart(self):
        joined,_=self.join('editor');self.accounts.member_change(self.a,self.b['id'],'viewer','fixture-personal-password')
        self.assertEqual(self.accounts.resolve(self.bt)['role'],'viewer')
        self.accounts.member_change(self.a,self.b['id'],'remove','fixture-personal-password');self.assertIsNone(self.accounts.resolve(self.bt))
        restarted=RegistryAccounts(self.path,self.connect)
        with self.connect() as db:self.assertIsNone(db.execute('SELECT role FROM catproof_members WHERE registry_id=%s AND user_id=%s',(self.a['workspace_id'],self.b['id'])).fetchone())
        token=restarted.login(self.b['email'],'fixture-personal-password')[1];self.assertEqual(restarted.resolve(token)['workspace_id'],self.b['workspace_id'])
    def test_idle_timeout_and_revoke_other_sessions(self):
        second=self.accounts.login(self.a['email'],'fixture-personal-password')[1]
        self.accounts.revoke_sessions(self.a,'others',self.at);self.assertIsNone(self.accounts.resolve(second));self.assertIsNotNone(self.accounts.resolve(self.at))
        with self.connect() as db:db.execute('UPDATE catproof_session_meta SET last_seen=%s WHERE token_hash=%s',(time.time()-1801,digest(self.at)))
        self.assertIsNone(self.accounts.resolve(self.at))
    def test_audit_and_durable_throttling(self):
        self.join();data=self.accounts.security(self.a,self.at);self.assertTrue(any(e['action']=='invitation.accepted' for e in data['events']))
        for _ in range(10):self.assertTrue(self.accounts.rate_allowed('same-identity'))
        restarted=RegistryAccounts(self.path,self.connect);self.assertFalse(restarted.rate_allowed('same-identity'))
        self.assertNotIn('code',str(data['invites']))
    def test_legacy_upgrade_retires_shared_access_without_losing_registry(self):
        _,token=self.accounts.legacy_login('neeraj');actor=self.accounts.resolve(token)
        with self.store.request(True,'legacy') as store:store.create({'name':'Original team agent'})
        upgraded=self.accounts.upgrade(actor,{'email':'personal@example.test','new_password':'personal-fixture-password'})
        self.assertIsNone(self.accounts.resolve(token));self.assertIsNone(self.accounts.legacy_login('mark'));self.assertIsNone(self.accounts.legacy_login('neeraj'))
        self.assertEqual(self.accounts.resolve(upgraded)['email'],'personal@example.test')
        with self.store.request(False,'legacy') as store:self.assertEqual(store.snapshot()['agents'][0]['name'],'Original team agent')
        self.assertIsNotNone(self.accounts.login('personal@example.test','personal-fixture-password'))
    def test_actor_is_committed_with_registry_event(self):
        with self.store.request(True,self.a['workspace_id']) as store:
            store.actor=self.a['email'];store.create({'name':'Attributable record'});store.actor=''
        with self.store.request(False,self.a['workspace_id']) as store:self.assertEqual(store.snapshot()['events'][0]['actor'],self.a['email'])
