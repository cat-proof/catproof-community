"""Peer credentials grant catalog reads only, with current owner authorization."""
import time
import unittest
import test_enterprise
from enterprise import digest


class PeerTokenTests(unittest.TestCase):
    setUp = test_enterprise.EnterpriseTests.setUp
    tearDown = test_enterprise.EnterpriseTests.tearDown
    signup = test_enterprise.EnterpriseTests.signup
    join = test_enterprise.EnterpriseTests.join

    def token(self):
        return self.accounts.create_peer_token(self.a, 'Receiving registry', 'fixture-personal-password')

    def test_digest_only_storage_registry_binding_and_revocation(self):
        result = self.token()
        actor = self.accounts.resolve_peer(result['token'])
        self.assertEqual(actor['workspace_id'], self.a['workspace_id'])
        self.assertEqual(actor['role'], 'viewer')
        self.assertIsNone(self.accounts.resolve(result['token']))
        self.assertIsNone(self.accounts.resolve_peer(self.at))
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT token_hash FROM catproof_peer_tokens').fetchone()[0], digest(result['token']))
        listing = self.accounts.security(self.a, self.at)
        self.assertNotIn(result['token'], str(listing))
        with self.assertRaises(ValueError):
            self.accounts.revoke_peer_token(self.b, result['id'], 'fixture-personal-password')
        self.accounts.revoke_peer_token(self.a, result['id'], 'fixture-personal-password')
        self.assertIsNone(self.accounts.resolve_peer(result['token']))

    def test_owner_and_personal_password_required(self):
        viewer, _ = self.join()
        with self.assertRaises(PermissionError):
            self.accounts.create_peer_token(viewer, 'Peer', 'fixture-personal-password')
        with self.assertRaises(PermissionError):
            self.accounts.create_peer_token(self.a, 'Peer', 'wrong-password')

    def test_expiry_and_creator_demotion_invalidate_tokens(self):
        expired = self.token()
        with self.connect() as db:
            db.execute('UPDATE catproof_peer_tokens SET expires_at=%s', (time.time()-1,))
        self.assertIsNone(self.accounts.resolve_peer(expired['token']))
        active = self.token()
        self.join('editor')
        self.accounts.member_change(self.a, self.b['id'], 'owner', 'fixture-personal-password')
        second = self.accounts.resolve(self.bt)
        self.accounts.member_change(second, self.a['id'], 'viewer', 'fixture-personal-password')
        self.assertIsNone(self.accounts.resolve_peer(active['token']))
        self.accounts.member_change(second, self.a['id'], 'owner', 'fixture-personal-password')
        self.assertIsNone(self.accounts.resolve_peer(active['token']), 'Promotion must not resurrect revoked tokens')
