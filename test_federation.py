from tests.db import registry as FederatedRegistry
import tempfile
import unittest
from pathlib import Path
from federation import identity,validate_origin,discover
from registry import ConnectorError,seed


def record(name='Logical Agent',status='active',rid='vendor-1',provenance='live'):
    return dict(id=rid,source_id=rid,name=name,source='Test vendor',kind='Agent',owner='source-owner',status=status,source_status=status,
                description='Observed purpose',provenance=provenance,last_seen='2026-10-27T18:00:00Z')
def payload(*rows):return {'agents':list(rows),'sources':[{'name':'Test vendor','health':'ok','mode':'live'}]}

class FederationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'db';self.store=FederatedRegistry(self.path,env={})
        self.a=self.store.add_connection({'name':'Registry A','kind':'atlas','url':'https://a.invalid'})
        self.b=self.store.add_connection({'name':'Registry B','kind':'atlas','url':'https://b.invalid'})
    def tearDown(self):self.store.db.close();self.tmp.cleanup()
    def test_canonical_exists_without_source_and_survives_restart(self):
        eid=self.store.create({'name':'My registered agent','business_owner':'Operations','lifecycle':'active'})
        self.store.reader=lambda *args:payload()
        self.store.refresh()
        reopened=FederatedRegistry(self.path,env={});a=next(a for a in reopened.snapshot()['agents'] if a['registry_id']==eid)
        self.assertEqual(a['business_owner'],'Operations');self.assertEqual(a['lifecycle'],'active');self.assertEqual(a['linked_records'],[]);reopened.db.close()
    def test_two_registries_same_names_are_separate_until_linked(self):
        self.store.ingest(self.a,payload(record()));self.store.ingest(self.b,payload(record()))
        before=self.store.snapshot();self.assertEqual(len(before['agents']),2)
        target=before['agents'][0]['registry_id'];oid=next(o['observation_id'] for o in before['observations'] if o['registry_id']!=target)
        self.store.link(oid,target);result=self.store.snapshot();canonical=next(a for a in result['agents'] if a['registry_id']==target)
        self.assertEqual(len(canonical['linked_records']),2);self.assertEqual(canonical['source_count'],2)
        self.assertEqual(len(result['agents']),2)  # source canonical record retained, not deleted
        self.assertIn('source_linked',[e['kind'] for e in result['events']])
    def test_canonical_owner_purpose_and_lifecycle_are_authoritative(self):
        self.store.ingest(self.a,payload(record()));eid=self.store.snapshot()['agents'][0]['registry_id']
        self.store.update(eid,{'business_owner':'Finance','purpose':'Our purpose','lifecycle':'suspended','name':'Canonical name'})
        source=record(name='Vendor changed',status='inactive');self.store.ingest(self.a,payload(source))
        a=self.store.snapshot()['agents'][0];self.assertEqual(a['registry_id'],eid);self.assertEqual(a['name'],'Canonical name')
        self.assertEqual(a['purpose'],'Our purpose');self.assertEqual(a['lifecycle'],'suspended');self.assertEqual(a['business_owner'],'Finance')
        self.assertEqual(a['linked_records'][0]['name'],'Vendor changed')
    def test_conflicting_statuses_are_explicit_and_split_is_reversible(self):
        self.store.ingest(self.a,payload(record()));self.store.ingest(self.b,payload(record(status='inactive')))
        s=self.store.snapshot();target=s['agents'][0]['registry_id'];oid=next(o['observation_id'] for o in s['observations'] if o['registry_id']!=target)
        result=self.store.link(oid,target);a=next(a for a in result['agents'] if a['registry_id']==target)
        self.assertEqual(a['status'],'conflict');self.assertIn('Conflicting source statuses',a['attention'])
        result=self.store.unlink(oid);self.assertEqual(len(next(a for a in result['agents'] if a['registry_id']==target)['linked_records']),1)
    def test_seeded_and_real_cannot_link(self):
        self.store.ingest(self.a,payload(record()));self.store.ingest(self.b,payload(record(provenance='seeded')))
        s=self.store.snapshot();target=next(a['registry_id'] for a in s['agents'] if a['provenance']=='live');oid=next(o['observation_id'] for o in s['observations'] if o['provenance']=='seeded')
        with self.assertRaises(ValueError):self.store.link(oid,target)
    def test_connection_failure_preserves_canonical_and_source_evidence(self):
        self.store.reader=lambda conn,env,force:payload(record(rid=conn['id']))
        before=self.store.refresh();ids={a['registry_id'] for a in before['agents']}
        def reader(conn,env,force):
            if conn['id']==self.a:raise ConnectorError('No access')
            return payload(record(rid=conn['id']))
        self.store.reader=reader;after=self.store.refresh()
        self.assertEqual(ids,{a['registry_id'] for a in after['agents']})
        self.assertTrue(next(o for o in after['observations'] if o['connection_id']==self.a)['stale'])
        self.assertFalse(next(o for o in after['observations'] if o['connection_id']==self.b)['stale'])
        self.assertEqual(len(after['runs']),4)
    def test_removal_is_per_connection_and_rediscovery_keeps_identity(self):
        self.store.ingest(self.a,payload(record()));self.store.ingest(self.b,payload(record()))
        before=self.store.snapshot();oid=identity(self.a,record());eid=next(o['registry_id'] for o in before['observations'] if o['observation_id']==oid)
        self.store.ingest(self.a,payload());s=self.store.snapshot()
        self.assertFalse(next(o for o in s['observations'] if o['observation_id']==oid)['present'])
        self.assertTrue(next(o for o in s['observations'] if o['connection_id']==self.b)['present'])
        self.store.ingest(self.a,payload(record()));self.assertEqual(eid,next(o['registry_id'] for o in self.store.snapshot()['observations'] if o['observation_id']==oid))
    def test_duplicate_response_is_atomic(self):
        with self.assertRaises(ValueError):self.store.ingest(self.a,payload(record(),record()))
        self.assertEqual(self.store.snapshot()['agents'],[])
    def test_pause_and_schema_validation(self):
        self.store.ingest(self.a,payload(record()));self.store.set_enabled(self.a,False)
        self.assertTrue(self.store.snapshot()['observations'][0]['stale'])
        with self.assertRaises(ValueError):self.store.refresh(self.a)
        with self.assertRaises(ValueError):self.store.create({'name':'x','lifecycle':'approved'})
        with self.assertRaises(ValueError):self.store.add_connection({'name':'unsafe','kind':'atlas','url':'https://token:secret@a.invalid'})
        with self.assertRaises(ValueError):validate_origin('http://127.0.0.1:8770',8770)
    def test_catalog_contract_and_csv_protection(self):
        self.store.create({'name':'=FORMULA','business_owner':'+Owner'})
        catalog=self.store.catalog();self.assertEqual(catalog['schema_version'],'atlas.registry.v1')
        self.assertEqual(catalog['agents'][0]['source_id'],self.store.snapshot()['agents'][0]['registry_id'])
        self.assertIn("'=FORMULA",self.store.export().decode('utf-8-sig'))
    def test_credentials_are_references_not_saved_values(self):
        cid=self.store.add_connection({'name':'Native DB','kind':'databricks','url':'https://db.invalid','credential_prefix':'DB'})
        with self.assertRaises(ConnectorError):discover(next(c for c in self.store.snapshot()['sources'] if c['id']==cid),{})
        self.assertNotIn('TOKEN_VALUE',str(self.store.snapshot()))
    def test_seed_orphan_keeps_demo_provenance(self):
        self.store.ingest(self.a,payload(record(provenance='seeded')));self.store.ingest(self.b,payload(record(provenance='seeded')))
        s=self.store.snapshot();target=s['agents'][0]['registry_id'];oid=next(o['observation_id'] for o in s['observations'] if o['registry_id']!=target)
        s=self.store.link(oid,target);self.assertTrue(all(a['provenance']=='seeded' for a in s['agents']))
    def test_peer_imports_are_not_reexported_and_legacy_import_is_idempotent(self):
        self.store.ingest(self.a,payload(record()))
        self.assertEqual(self.store.catalog()['agents'],[])
        import sqlite3,json
        old=sqlite3.connect(Path(self.tmp.name)/'old.db')
        old.execute('CREATE TABLE agents(namespace TEXT,payload TEXT,business_owner TEXT,review TEXT,notes TEXT,reviewed_at TEXT)')
        old.execute('INSERT INTO agents VALUES(?,?,?,?,?,?)',('legacy',json.dumps(record()),'Prior local owner','reviewed','Preserved local decision','2026-10-27T00:00:00Z'))
        old.commit();old.close()
        self.assertEqual(self.store.import_legacy(Path(self.tmp.name)/'old.db','legacy'),1)
        self.assertEqual(self.store.import_legacy(Path(self.tmp.name)/'old.db','legacy'),0)
        self.assertEqual(self.store.snapshot()['agents'][0]['business_owner'],'Prior local owner')
