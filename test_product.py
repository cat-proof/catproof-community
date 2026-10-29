import json
import tempfile
import unittest
from pathlib import Path
from tests.db import product_store as ProductStore
from registry import seed


def snapshot(rows=None,health='ok'):
    return {'agents':seed('Databricks') if rows is None else rows,'sources':[{'name':'Databricks','mode':'seeded','health':health}]}

class ProductTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'registry.sqlite3';self.store=ProductStore(self.path,'workspace-a')
    def tearDown(self):
        self.store.db.close();self.tmp.cleanup()
    def test_persistence_and_annotation_survive_source_refresh(self):
        initial=self.store.ingest(snapshot());a=initial['agents'][0]
        self.store.annotate(a['registry_id'],{'business_owner':'Finance','review':'reviewed','notes':'Validate outputs'})
        updated=self.store.ingest(snapshot()); saved=next(x for x in updated['agents'] if x['registry_id']==a['registry_id'])
        self.assertEqual(saved['business_owner'],'Finance');self.assertEqual(saved['review'],'reviewed')
        self.assertEqual(saved['owner'],a['owner']);self.assertTrue(saved['reviewed_at'])
        reopened=ProductStore(self.path,'workspace-a')
        self.assertEqual(len(reopened.snapshot()['agents']),3);reopened.db.close()
    def test_last_seen_does_not_generate_change_noise(self):
        first=self.store.ingest(snapshot());rows=seed('Databricks')
        for row in rows:row['last_seen']='2030-01-01T00:00:00Z'
        second=self.store.ingest(snapshot(rows));self.assertEqual(len(first['events']),len(second['events']))
    def test_source_change_removal_and_rediscovery(self):
        self.store.ingest(snapshot());rows=seed('Databricks');rows[0]['status']='error'
        result=self.store.ingest(snapshot(rows[:1]));kinds=[e['kind'] for e in result['events']]
        self.assertIn('source_changed',kinds);self.assertEqual(kinds.count('not_observed'),2)
        self.assertEqual(sum(a['present'] for a in result['agents']),1)
        result=self.store.ingest(snapshot(rows));self.assertEqual(sum(e['kind']=='rediscovered' for e in result['events']),2)
    def test_failed_source_does_not_remove_agents(self):
        self.store.ingest(snapshot());result=self.store.ingest(snapshot([],health='error'))
        self.assertTrue(all(a['present'] for a in result['agents']))
    def test_workspace_and_provenance_are_isolated(self):
        self.store.ingest(snapshot());rows=seed('Databricks')
        for row in rows:row['provenance']='live'
        result=self.store.ingest(snapshot(rows));self.assertEqual(len(result['agents']),6)
        other=ProductStore(self.path,'workspace-b');self.assertEqual(other.snapshot()['agents'],[]);other.db.close()
    def test_invalid_update_and_csv_formula_protection(self):
        result=self.store.ingest(snapshot());rid=result['agents'][0]['registry_id']
        with self.assertRaises(ValueError):self.store.annotate(rid,{'review':'certified'})
        with self.assertRaises(ValueError):self.store.annotate(rid,{'owner':'overwrite-vendor'})
        self.store.annotate(rid,{'business_owner':'=EVIL()'})
        self.assertIn("'=EVIL()",self.store.export(True).decode('utf-8-sig'))
    def test_duplicate_ingest_rolls_back(self):
        self.store.ingest(snapshot());rows=seed('Databricks');rows[0]['name']='different'
        with self.assertRaises(ValueError):self.store.ingest(snapshot([rows[0],rows[0]]))
        self.assertNotIn('different',[a['name'] for a in self.store.snapshot()['agents']])
