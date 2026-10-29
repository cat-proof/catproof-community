import hashlib,json,time
from account_recovery import AccountRecovery
import unittest
import test_enterprise

class RecoveryTests(unittest.TestCase):
    setUp=test_enterprise.EnterpriseTests.setUp
    tearDown=test_enterprise.EnterpriseTests.tearDown
    signup=test_enterprise.EnterpriseTests.signup
    def grant(self,token='x'*43,seconds=600):
        return json.dumps([{'email':self.a['email'],'token_hash':hashlib.sha256(token.encode()).hexdigest(),'expires_at':time.time()+seconds}])
    def test_bound_single_use_reset_preserves_registry_and_revokes_sessions(self):
        with self.store.request(True,self.a['workspace_id']) as registry:registry.create({'name':'Keep this record'})
        cfg=self.grant();r=AccountRecovery(self.accounts,cfg)
        for email,token in ((self.b['email'],'x'*43),(self.a['email'],'z'*43)):
            with self.assertRaises(PermissionError):r.reset(email,token,'new-fixture-password')
        with self.assertRaises(ValueError):r.reset(self.a['email'],'x'*43,'short')
        r.reset(self.a['email'],'x'*43,'new-fixture-password')
        self.assertIsNone(self.accounts.resolve(self.at));self.assertIsNone(self.accounts.login(self.a['email'],'fixture-personal-password'))
        self.assertIsNotNone(self.accounts.login(self.a['email'],'new-fixture-password'))
        with self.store.request(False,self.a['workspace_id']) as registry:self.assertEqual(registry.snapshot()['agents'][0]['name'],'Keep this record')
        # Replaying the same deployment configuration cannot restore a consumed grant.
        r=AccountRecovery(self.accounts,cfg)
        with self.assertRaises(PermissionError):r.reset(self.a['email'],'x'*43,'another-fixture-password')
        self.assertIsNotNone(self.accounts.resolve(self.bt))
    def test_expired_missing_and_malformed_grants_fail_closed(self):
        r=AccountRecovery(self.accounts,self.grant())
        with self.connect() as db:db.execute('UPDATE catproof_account_recovery SET expires_at=0')
        with self.assertRaises(PermissionError):r.reset(self.a['email'],'x'*43,'new-fixture-password')
        for token in ('',None,'short','x'*101):
            with self.assertRaises(PermissionError):r.reset(self.a['email'],token,'new-fixture-password')
        AccountRecovery(self.accounts,self.grant(token='y'*43,seconds=-1))
        with self.assertRaises(PermissionError):r.reset(self.a['email'],'y'*43,'new-fixture-password')

    def test_self_service_issue_hashes_tokens_and_success_revokes_all_pending_links(self):
        r=AccountRecovery(self.accounts)
        self.assertIsNone(r.issue('unknown@example.test'))
        first=r.issue(self.a['email']);second=r.issue(self.a['email'])
        with self.connect() as db:
            rows=db.execute('SELECT token_hash FROM catproof_account_recovery').fetchall()
            self.assertNotIn(first,str(rows));self.assertNotIn(second,str(rows))
        r.reset(self.a['email'],first,'new-fixture-password')
        with self.assertRaises(PermissionError):r.reset(self.a['email'],second,'another-fixture-password')
