"""Remove explicitly retired source data without contacting any provider.

The immutable fingerprint identifies one retired origin without publishing it.
This is an operator migration, not a customer-facing deletion endpoint.
"""
import hashlib
from urllib.parse import urlsplit

RETIRED_IDS=frozenset({'conn_a1e0ec045644436f'})
RETIRED_HOSTS=frozenset({'481056482218033339f4f3589f35d85f53b8deaeb20cc1427896b8e1f747f00a'})


def retired_origin(origin):
    host=(urlsplit(origin).hostname or '').lower()
    return hashlib.sha256(host.encode()).hexdigest() in RETIRED_HOSTS


def purge_retired(db, ids=RETIRED_IDS, origin_check=retired_origin):
    """Caller owns the transaction. Preserve manual and independently linked records."""
    retired=[r[0] for r in db.execute('SELECT id,url FROM catproof_connections') if r[0] in ids or origin_check(r[1])]
    removed_entities=0;removed_credentials=0
    for cid in retired:
        entities=[r[0] for r in db.execute('SELECT b.entity_id FROM catproof_bindings b JOIN catproof_observations o ON o.id=b.observation_id WHERE o.connection_id=? UNION SELECT entity_id FROM catproof_registry_events WHERE connection_id=? AND entity_id IS NOT NULL',(cid,cid))]
        evidence_ids=[cid]+[r[0] for r in db.execute('SELECT id FROM catproof_observations WHERE connection_id=? UNION ALL SELECT id FROM catproof_assets WHERE connection_id=?',(cid,cid))]
        db.execute('DELETE FROM catproof_bindings WHERE observation_id IN (SELECT id FROM catproof_observations WHERE connection_id=?)',(cid,))
        db.execute('DELETE FROM catproof_asset_links WHERE asset_id IN (SELECT id FROM catproof_assets WHERE connection_id=?)',(cid,))
        db.execute('DELETE FROM catproof_assets WHERE connection_id=?',(cid,))
        db.execute('DELETE FROM catproof_observations WHERE connection_id=?',(cid,))
        db.execute('DELETE FROM catproof_sync_runs WHERE connection_id=?',(cid,))
        # Link/unlink history can carry source IDs in JSON even without connection_id.
        db.execute('DELETE FROM catproof_registry_events WHERE connection_id=?',(cid,))
        for evidence_id in evidence_ids:
            db.execute('DELETE FROM catproof_registry_events WHERE strpos(detail::text,?)>0',(evidence_id,))
        removed_credentials+=db.execute("DELETE FROM catproof_connector_secrets WHERE registry_id=current_setting('catproof.registry_id',true) AND connection_id=?",(cid,)).rowcount
        db.execute('DELETE FROM catproof_connections WHERE id=?',(cid,))
        for eid in entities:
            linked=db.execute('SELECT 1 FROM catproof_bindings WHERE entity_id=? UNION ALL SELECT 1 FROM catproof_asset_links WHERE entity_id=?',(eid,eid)).fetchone()
            entity=db.execute('SELECT manual FROM catproof_entities WHERE id=?',(eid,)).fetchone()
            if not linked and entity and not entity[0]:
                db.execute('DELETE FROM catproof_registry_events WHERE entity_id=?',(eid,))
                db.execute('DELETE FROM catproof_entities WHERE id=?',(eid,));removed_entities+=1
    return {'connections':retired,'entities_removed':removed_entities,'credentials_removed':removed_credentials}
