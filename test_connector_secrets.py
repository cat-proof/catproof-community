import json
import tempfile
import unittest
from pathlib import Path
from cryptography.fernet import Fernet
from connector_secrets import ConnectorSecrets
from enterprise import RegistryAccounts
from hosted_store import HostedStore
from test_accounts import PostgreSQLConnection

class SecretTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=str(Path(self.tmp.name)/'db');self.connect=lambda *a,**kw:PostgreSQLConnection(self.path)
        self.store=HostedStore(self.path,self.connect);self.accounts=RegistryAccounts(self.path,self.connect)
        self.a,self.at=self.signup('owner@example.test');self.b,self.bt=self.signup('other@example.test');self.initialize_secrets()
    def initialize_secrets(self):
        self.key=Fernet.generate_key().decode();self.secrets=ConnectorSecrets(self.accounts,self.key);self.store.credentials=self.secrets
    def tearDown(self):self.store.store.db.close();self.tmp.cleanup()
    def signup(self,email):
        _,token=self.accounts.register({'name':'Test','email':email,'workspace':'Test registry','password':'fixture-personal-password'})
        return self.accounts.resolve(token),token
    def save(self,workspace='workspace-one',key='fixture-provider-key-one',actor=None):
        return self.secrets.change(actor or self.a,{'name':'My Lovable','workspace_id':workspace,'api_key':key},connect=True)['connection_id']
    def test_encrypted_scoped_storage_never_enters_snapshot_or_audit(self):
        cid=self.save()
        with self.connect() as db:
            cipher=db.execute('SELECT ciphertext FROM catproof_connector_secrets').fetchone()[0]
            self.assertNotIn('fixture-provider-key-one',cipher)
        with self.store.request(False,self.a['workspace_id']) as store:
            source=store.snapshot()['sources'][0];self.assertTrue(source['credential_configured']);self.assertEqual(source['credential_storage'],'encrypted')
            self.assertNotIn('fixture-provider-key-one',str(store.snapshot()));self.assertNotIn(b'fixture-provider-key-one',store.export())
        self.assertEqual(store.credentials,{})
        with self.store.request(False,self.b['workspace_id']) as store:self.assertEqual(store.snapshot()['sources'],[])
        self.assertNotIn('fixture-provider-key-one',str(self.accounts.security(self.a,self.at)))
    def test_duplicate_create_cannot_replace_credentials(self):
        cid=self.save()
        with self.assertRaises(ValueError):self.save(key='fixture-replacement-key')
        with self.store.request(False,self.a['workspace_id']) as store:
            self.assertEqual(len(store.snapshot()['sources']),1)
            self.assertEqual(store.credentials[cid],'fixture-provider-key-one')
        self.secrets.change(self.a,{'connection_id':cid,'api_key':'fixture-replacement-key'})
        with self.store.request(False,self.a['workspace_id']) as store:
            self.assertEqual(store.credentials[cid],'fixture-replacement-key')
    def test_duplicate_http_create_returns_scoped_id_without_replacing_key(self):
        import threading
        from urllib.request import Request,urlopen
        from urllib.error import HTTPError
        from hosted_app import create_server
        cid=self.save()
        with self.store.request(True,self.a['workspace_id']) as store:store.set_enabled(cid,False)
        server=create_server({'APP_PASSWORD':'fixture-bootstrap-password','APP_ORIGIN':'https://registry.example','CONNECTOR_ENCRYPTION_KEYS':self.key},self.store,('127.0.0.1',0),accounts=self.accounts)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            body={'name':'Duplicate','kind':'lovable','url':'https://api.lovable.dev','config':{'workspace_id':'workspace-one'},'credentials':{'mode':'token','token':'fixture-must-not-replace-key'}}
            req=Request('http://127.0.0.1:'+str(server.server_port)+'/api/credentials/connect',data=json.dumps(body).encode(),headers={'Host':'registry.example','Origin':'https://registry.example','X-Registry-Action':'refresh','Content-Type':'application/json','Cookie':'catproof_session='+self.at})
            with self.assertRaises(HTTPError) as failed:urlopen(req)
            self.assertEqual(failed.exception.code,409)
            response=json.load(failed.exception)
            self.assertEqual(response['connection_id'],cid)
            self.assertNotIn('fixture-must-not-replace-key',json.dumps(response))
            with self.store.request(False,self.a['workspace_id']) as store:
                self.assertEqual(len(store.snapshot()['sources']),1)
                self.assertFalse(store.snapshot()['sources'][0]['enabled'])
                self.assertEqual(store.credentials[cid],'fixture-provider-key-one')
        finally:server.shutdown();server.server_close();thread.join()
    def test_two_connections_with_same_prefix_use_their_own_keys(self):
        first=self.save();second=self.save('workspace-two','fixture-second-key');observed=[]
        def reader(connection,env,force):observed.append((connection['id'],env['LOVABLE_TOKEN']));return {'agents':[],'assets':[],'sources':[]}
        with self.store.request(True,self.a['workspace_id']) as store:
            store.reader=reader
            store.refresh()
        self.assertEqual(dict(observed),{first:'fixture-provider-key-one',second:'fixture-second-key'})
        self.assertEqual(store.credentials,{})
    def test_fresh_owner_and_cross_registry_connection_checks(self):
        cid=self.save()
        with self.assertRaises(ValueError):self.secrets.change(self.b,{'connection_id':cid,'api_key':'fixture-unauthorized'})
        invite=self.accounts.invite(self.a,self.b['email'],'editor','fixture-personal-password');self.accounts.accept(self.b,invite['code'],self.bt);editor=self.accounts.resolve(self.bt)
        with self.assertRaises(PermissionError):self.secrets.change(editor,{'connection_id':cid,'api_key':'fixture-unauthorized'})
        with self.connect() as db:db.execute("UPDATE catproof_members SET role='viewer' WHERE registry_id=%s AND user_id=%s",(self.a['workspace_id'],self.a['id']))
        with self.assertRaises(PermissionError):self.save()
    def test_invalid_key_rolls_back_new_connection_and_missing_master_fails_closed(self):
        with self.assertRaises(ValueError):self.save(key='short')
        with self.store.request(False,self.a['workspace_id']) as store:self.assertEqual(store.snapshot()['sources'],[])
        unavailable=ConnectorSecrets(self.accounts,'')
        with self.assertRaises(ValueError):unavailable.change(self.a,{'name':'New','workspace_id':'workspace-new','api_key':'fixture-valid-key'},connect=True)
        with self.store.request(False,self.a['workspace_id']) as store:self.assertEqual(store.snapshot()['sources'],[])
        self.save();self.store.credentials=unavailable
        with self.assertRaises(RuntimeError):
            with self.store.request(False,self.a['workspace_id']):pass
    def test_ciphertext_cannot_move_between_connections_and_rotation_works(self):
        first=self.save();second=self.save('workspace-two','fixture-second-key')
        other_key=Fernet.generate_key().decode();rotated=ConnectorSecrets(self.accounts,other_key+','+self.key)
        with self.connect() as db:self.assertEqual(rotated.load(db,self.a['workspace_id'])[first],'fixture-provider-key-one')
        with self.connect() as db:
            cipher=db.execute('SELECT ciphertext FROM catproof_connector_secrets WHERE connection_id=%s',(first,)).fetchone()[0]
            db.execute('UPDATE catproof_connector_secrets SET ciphertext=%s WHERE connection_id=%s',(cipher,second))
        with self.assertRaises(RuntimeError):
            with self.store.request(False,self.a['workspace_id']):pass
    def test_remove_key_pauses_connection_and_retains_inventory(self):
        cid=self.save();self.secrets.change(self.a,{'connection_id':cid},remove=True)
        with self.store.request(False,self.a['workspace_id']) as store:
            self.assertFalse(store.snapshot()['sources'][0]['enabled']);self.assertFalse(store.snapshot()['sources'][0]['credential_configured'])
        with self.connect() as db:self.assertEqual(db.execute('SELECT ciphertext FROM catproof_connector_secrets').fetchone()[0],'')
    def connect_provider(self,kind,credentials,config=None,url='https://provider.example'):
        return self.secrets.change(self.a,{'name':'Synthetic '+kind,'kind':kind,'url':url,'config':config or {},'credentials':credentials},connect=True)['connection_id']
    def test_all_remote_catalog_connectors_encrypt_route_and_remove_credentials(self):
        from adapters.catalog import CATALOG,BASE_CATALOG
        from adapters.auth import validate_credentials
        for kind,spec in {**BASE_CATALOG,**CATALOG}.items():
            if kind=='local':continue
            with self.subTest(kind=kind):
                config={f['key']:f['example'] for f in spec['fields'] if f['required'] or f.get('required_on_create')}
                credentials={'mode':'aws','access_key_id':'fixture-access-key','secret_access_key':'fixture-aws-secret','session_token':'fixture-session-token'} if kind.startswith('aws_') else {'mode':'token','token':'fixture-provider-secret-'+kind}
                cid=self.connect_provider(kind,credentials,config,'https://api.lovable.dev' if kind=='lovable' else 'https://provider.example')
                expected=validate_credentials(kind,credentials)
                with self.store.request(False,self.a['workspace_id']) as store:
                    connection=dict(store.db.execute('SELECT * FROM catproof_connections WHERE id=?',(cid,)).fetchone());env=store.credential_env(connection)
                    for suffix,value in expected.items():
                        self.assertEqual(env[kind.upper()+'_'+suffix],value)
                        self.assertNotIn(value,str(store.snapshot()));self.assertNotIn(value.encode(),store.export())
                    self.assertTrue(next(s for s in store.snapshot()['sources'] if s['id']==cid)['credential_configured'])
                with self.connect() as db:
                    cipher=db.execute('SELECT ciphertext FROM catproof_connector_secrets WHERE connection_id=%s',(cid,)).fetchone()[0]
                    for value in expected.values():self.assertNotIn(value,cipher)
                self.secrets.change(self.a,{'connection_id':cid},remove=True)
                with self.store.request(False,self.a['workspace_id']) as store:self.assertFalse(next(s for s in store.snapshot()['sources'] if s['id']==cid)['enabled'])
    def test_basic_password_is_opaque_and_switch_clears_previous_auth(self):
        cid=self.connect_provider('servicenow',{'mode':'basic','username':'fixture-reader','password':' secret with spaces '})
        with self.store.request(False,self.a['workspace_id']) as store:
            c=dict(store.db.execute('SELECT * FROM catproof_connections WHERE id=?',(cid,)).fetchone());store.env={'SERVICENOW_TOKEN':'must-not-fallback'}
            env=store.credential_env(c);self.assertNotIn('SERVICENOW_TOKEN',env);self.assertEqual(env['SERVICENOW_PASSWORD'],' secret with spaces ')
        self.secrets.change(self.a,{'connection_id':cid,'credentials':{'mode':'token','token':'fixture-bearer-token'}})
        with self.store.request(False,self.a['workspace_id']) as store:
            c=dict(store.db.execute('SELECT * FROM catproof_connections WHERE id=?',(cid,)).fetchone());self.assertNotIn('SERVICENOW_PASSWORD',store.credential_env(c))
    def test_public_auth_never_falls_back_and_invalid_fields_rollback(self):
        cid=self.connect_provider('a2a',{'mode':'token','token':'fixture-card-token'})
        self.secrets.change(self.a,{'connection_id':cid,'credentials':{'mode':'none'}})
        with self.store.request(False,self.a['workspace_id']) as store:
            c=dict(store.db.execute('SELECT * FROM catproof_connections WHERE id=?',(cid,)).fetchone());self.assertEqual(c['credential_prefix'],'');self.assertFalse(store.has_credentials(c))
        invalid=[('aws_bedrock',{'mode':'aws','access_key_id':'fixture'}, {'region':'us-east-1'}),('servicenow',{'mode':'basic','username':'fixture'},{}),('databricks',{'mode':'none'},{}),('databricks',{'mode':'token','token':'fixture','PROFILE':'evil'},{}),('databricks',{'mode':'token','token':'fixture\nheader'}, {})]
        for kind,credentials,config in invalid:
            with self.subTest(kind=kind),self.assertRaises(ValueError):self.connect_provider(kind,credentials,config)
        with self.store.request(False,self.a['workspace_id']) as store:self.assertEqual(len(store.snapshot()['sources']),1)
    def test_generic_credentials_reject_other_registry_and_non_owner(self):
        cid=self.connect_provider('servicenow',{'mode':'basic','username':'fixture','password':'fixture-password'})
        body={'connection_id':cid,'credentials':{'mode':'token','token':'fixture-stolen'}}
        with self.assertRaises(ValueError):self.secrets.change(self.b,body)
        invite=self.accounts.invite(self.a,self.b['email'],'editor','fixture-personal-password');self.accounts.accept(self.b,invite['code'],self.bt)
        with self.assertRaises(PermissionError):self.secrets.change(self.accounts.resolve(self.bt),body)
    def test_removed_credentials_block_legacy_environment_fallback(self):
        cid=self.connect_provider('servicenow',{'mode':'token','token':'fixture-managed-token'})
        self.secrets.change(self.a,{'connection_id':cid},remove=True)
        with self.store.request(False,self.a['workspace_id']) as store:
            store.env={'SERVICENOW_TOKEN':'fixture-legacy-token','SERVICENOW_USERNAME':'fixture-old','SERVICENOW_PASSWORD':'fixture-old-password'}
            c=dict(store.db.execute('SELECT * FROM catproof_connections WHERE id=?',(cid,)).fetchone());env=store.credential_env(c)
            self.assertFalse(store.has_credentials(c));self.assertNotIn('SERVICENOW_TOKEN',env);self.assertNotIn('SERVICENOW_PASSWORD',env)
            self.assertEqual(store.snapshot()['sources'][0]['credential_storage'],'none')

    def test_hosted_worker_receives_connection_scoped_keys_without_snapshot_leaks(self):
        first=self.save();second=self.save('workspace-two','fixture-second-key');seen={}
        def fetch(connection,env,timeout):
            seen[connection['id']]=env['LOVABLE_TOKEN']
            self.assertNotIn('CONNECTOR_ENCRYPTION_KEYS',env)
            self.assertNotIn('fixture-provider-key-one',str(connection))
            return {'agents':[],'assets':[],'sources':[]}
        result=self.store.refresh(self.a['workspace_id'],None,lambda:self.a,fetch)
        self.assertEqual(seen,{first:'fixture-provider-key-one',second:'fixture-second-key'})
        for key in seen.values():self.assertNotIn(key,str(result))

    def test_key_replacement_or_removal_discards_inflight_sync(self):
        from test_federation import payload,record
        for remove in (False,True):
            with self.subTest(remove=remove):
                cid=self.save(workspace='race-'+str(remove))
                def fetch(connection,env,timeout):
                    self.secrets.change(self.a,{'connection_id':cid} if remove else
                        {'connection_id':cid,'api_key':'fixture-replaced-key'},remove=remove)
                    return payload(record(name='Superseded credential result'))
                result=self.store.refresh(self.a['workspace_id'],cid,lambda:self.a,fetch)
                self.assertEqual(result['agents'],[])
                self.assertEqual(result['runs'],[])

    def test_credential_rotation_preserves_existing_records(self):
        cid=self.save()
        with self.store.request(True,self.a['workspace_id']) as store:
            store.create({'name':'Before key rotation'})
        self.secrets.change(self.a,{'connection_id':cid,'api_key':'fixture-upgraded-key'})
        with self.store.request(False,self.a['workspace_id']) as store:
            self.assertEqual(store.credentials[cid],'fixture-upgraded-key')
