import json
import tempfile
import time
import unittest
from pathlib import Path
from enterprise import RegistryAccounts
from hosted_store import HostedStore
from local_devices import LocalDevices,SlowReport,metadata
from test_accounts import PostgreSQLConnection

def report(sequence=1):
    return {'schema':'catproof.local.v1','sequence':sequence,'platform':'macOS','tools':[{'tool':'codex','installed':True,'version':'1.0.0','agents':['reviewer'],'mcp_servers':['docs']}],'sessions':[{'id':'a'*64,'tool':'codex','event':'SessionStart','at':time.time()}]}

class LocalDeviceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=str(Path(self.tmp.name)/'db');self.connect=lambda *a,**kw:PostgreSQLConnection(self.path)
        self.store=HostedStore(self.path,self.connect);self.accounts=RegistryAccounts(self.path,self.connect);self.devices=LocalDevices(self.accounts)
        self.a,self.at=self.signup('owner@example.test');self.b,self.bt=self.signup('other@example.test')
    def tearDown(self):self.store.store.db.close();self.tmp.cleanup()
    def signup(self,email):
        _,token=self.accounts.register({'name':'Test','email':email,'workspace':'Test registry','password':'fixture-personal-password'})
        return self.accounts.resolve(token),token
    def enroll(self):
        p=self.devices.pair(self.a,'Personal laptop','fixture-personal-password');return self.devices.enroll(p['code']),p
    def test_one_time_pairing_and_hash_only_device_scope(self):
        device,p=self.enroll()
        with self.assertRaises(PermissionError):self.devices.enroll(p['code'])
        self.assertEqual(self.devices.listing(self.b)['devices'],[])
        self.devices.report(device['token'],report());listed=self.devices.listing(self.a)
        self.assertEqual(listed['devices'][0]['owner'],self.a['email']);self.assertEqual(listed['devices'][0]['status'],'reporting')
        self.assertNotIn(device['token'],str(listed));self.assertNotIn(p['code'],str(listed))
        self.assertIsNone(self.accounts.resolve(device['token']))
        with self.connect() as db:
            self.assertNotEqual(db.execute('SELECT token_hash FROM catproof_local_devices').fetchone()[0],device['token'])
            self.assertNotEqual(db.execute('SELECT code_hash FROM catproof_local_pairings').fetchone()[0],p['code'])
    def test_owner_password_fresh_role_and_pending_cancel(self):
        with self.assertRaises(PermissionError):self.devices.pair(self.a,'Laptop','bad-password')
        invitation=self.accounts.invite(self.a,self.b['email'],'viewer','fixture-personal-password');self.accounts.accept(self.b,invitation['code'],self.bt);viewer=self.accounts.resolve(self.bt)
        with self.assertRaises(PermissionError):self.devices.pair(viewer,'Laptop','fixture-personal-password')
        p=self.devices.pair(self.a,'Laptop','fixture-personal-password')
        with self.assertRaises(PermissionError):self.devices.revoke(viewer,p['id'],'fixture-personal-password',pairing=True)
        self.devices.revoke(self.a,p['id'],'fixture-personal-password',pairing=True)
        with self.assertRaises(PermissionError):self.devices.enroll(p['code'])
        p=self.devices.pair(self.a,'Laptop','fixture-personal-password')
        with self.connect() as db:db.execute("UPDATE catproof_members SET role='viewer' WHERE registry_id=%s AND user_id=%s",(self.a['workspace_id'],self.a['id']))
        with self.assertRaises(PermissionError):self.devices.enroll(p['code'])
    def test_expiry_revocation_and_cross_registry_revoke(self):
        device,_=self.enroll()
        with self.assertRaises(ValueError):self.devices.revoke(self.b,device['device_id'],'fixture-personal-password')
        self.devices.revoke(self.a,device['device_id'],'fixture-personal-password')
        with self.assertRaises(PermissionError):self.devices.report(device['token'],report())
        self.assertEqual(self.devices.listing(self.a)['devices'][0]['status'],'revoked')
        device,_=self.enroll()
        with self.connect() as db:db.execute('UPDATE catproof_local_devices SET expires_at=0 WHERE id=%s',(device['device_id'],))
        with self.assertRaises(PermissionError):self.devices.report(device['token'],report())
        p=self.devices.pair(self.a,'Expired','fixture-personal-password')
        with self.connect() as db:db.execute('UPDATE catproof_local_pairings SET expires_at=0')
        with self.assertRaises(PermissionError):self.devices.enroll(p['code'])
    def test_sequence_throttle_presence_and_audit(self):
        d,_=self.enroll();self.devices.report(d['token'],report())
        with self.assertRaises(ValueError):self.devices.report(d['token'],report())
        with self.assertRaises(SlowReport):self.devices.report(d['token'],report(2))
        with self.connect() as db:db.execute('UPDATE catproof_local_devices SET last_seen=%s',(time.time()-181,))
        self.assertEqual(self.devices.listing(self.a)['devices'][0]['status'],'offline')
        self.devices.report(d['token'],report(3));self.assertEqual(self.devices.listing(self.a)['devices'][0]['status'],'reporting')
        actions=[e['action'] for e in self.accounts.security(self.a,self.at)['events']]
        self.assertIn('local.inventory_changed',actions);self.assertIn('local.enrolled',actions)
    def test_metadata_rejects_sensitive_or_unbounded_fields(self):
        cases=[]
        for field in ('prompt','cwd','transcript_path','token'):
            p=report();p[field]='private';cases.append(p)
        p=report();p['tools'][0]['headers']={'Authorization':'private'};cases.append(p)
        p=report();p['tools'][0]['agents']=['name']*51;cases.append(p)
        p=report();p['sequence']=True;cases.append(p)
        p=report();p['sessions'][0]['at']=float('nan');cases.append(p)
        p=report();p['sessions'][0]['at']=time.time()-90000;cases.append(p)
        p=report();p['sessions'][0]['cwd']='/private';cases.append(p)
        for p in cases:
            with self.subTest(payload=p),self.assertRaises(ValueError):metadata(p)
        self.assertNotIn('private',metadata(report()))
