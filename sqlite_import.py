"""Explicit, transactional import of offline standalone SQLite backups.

This is an operator command, never startup discovery. No provider is contacted.
The source is immutable; unknown schemas and occupied destinations fail closed.
"""
from contextlib import contextmanager
import datetime
import hashlib
import json
from pathlib import Path
import sqlite3

import sqlalchemy as sa
from database import engine_for, HOSTED_HEAD, VERSION_TABLE
from legacy_import import TABLES as FEDERATED_TABLES, PRODUCT_TABLES, ImportRefused, source_layout, validate_source
from retired_sources import RETIRED_IDS, retired_origin

ALL_TABLES=FEDERATED_TABLES+PRODUCT_TABLES


@contextmanager
def offline_source(path):
    path=Path(path).expanduser().resolve(strict=True)
    if not path.is_file():raise ImportRefused('Source must be an offline SQLite backup file')
    def fingerprint():
        if any(Path(str(path)+suffix).exists() for suffix in ('-wal','-shm','-journal')):
            raise ImportRefused('SQLite journal sidecars found; stop the old app and create a standalone SQLite backup first')
        with path.open('rb') as handle:return hashlib.file_digest(handle,'sha256').hexdigest()
    digest=fingerprint()
    source=sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True)
    raw=sqlite3.connect(':memory:')
    try:
        source.backup(raw)
        raw.row_factory=sqlite3.Row
        raw.execute('PRAGMA query_only=ON')
        if fingerprint()!=digest:raise ImportRefused('Source changed while reading; stop all old writers first')
        if [r[0] for r in raw.execute('PRAGMA integrity_check')]!=['ok']:
            raise ImportRefused('SQLite integrity check failed')
        if raw.execute('PRAGMA foreign_key_check').fetchone():raise ImportRefused('SQLite foreign key check failed')
        yield raw,digest
    finally:
        raw.close();source.close()


def import_sqlite(path,registry_id,url=None,creator=None):
    """Import all source rows into an empty, explicitly selected registry."""
    if not registry_id or not registry_id.strip():raise ImportRefused('An explicit target registry ID is required')
    with offline_source(path) as (raw,digest):
        kind,names=source_layout(raw)
        counts={name:raw.execute('SELECT count(*) FROM '+name).fetchone()[0] for name in names}
        marker='sqlite-file:v1:'+kind+':'+digest
        engine=engine_for(url,creator)
        try:
            with engine.begin() as db:
                if db.exec_driver_sql('SELECT version_num FROM '+VERSION_TABLE).scalars().all()!=[HOSTED_HEAD]:
                    raise ImportRefused('Run python database.py upgrade before importing')
                db.exec_driver_sql("SET LOCAL lock_timeout = '5s'")
                db.execute(sa.text("SELECT set_config('catproof.registry_id',:tenant,true)"),{'tenant':registry_id})
                # Match application locks, then block standalone writers too. Identity
                # sequence repair must not race inserts into other registries.
                key=int.from_bytes(hashlib.sha256(('catproof:registry:'+registry_id).encode()).digest()[:8],'big',signed=True)
                db.execute(sa.text('SELECT pg_advisory_xact_lock(:key)'),{'key':key})
                db.exec_driver_sql('LOCK TABLE '+','.join('catproof_'+name for name in ALL_TABLES)+' IN SHARE ROW EXCLUSIVE MODE')
                if not db.execute(sa.text('SELECT 1 FROM catproof_workspaces WHERE id=:tenant'),{'tenant':registry_id}).first():
                    raise ImportRefused('Target registry does not exist; create it before importing')
                params={'tenant':registry_id,'marker':marker}
                if db.execute(sa.text('SELECT 1 FROM catproof_migrations WHERE tenant_id=:tenant AND id=:marker'),params).first():
                    return {'status':'already_imported','format':kind,'counts':counts}
                for name in ALL_TABLES:
                    if db.execute(sa.text('SELECT 1 FROM catproof_'+name+' WHERE tenant_id=:tenant LIMIT 1'),params).first():
                        raise ImportRefused('Target registry already contains data; use an empty registry')
                metadata=sa.MetaData()
                targets={name:sa.Table('catproof_'+name,metadata,autoload_with=db) for name in names}
                validate_source(raw,targets,kind)
                # File imports run after Alembic's retirement cleanup. Reject the
                # entire backup rather than restoring retired sources or dropping rows.
                if kind=='federated':
                    for row in raw.execute('SELECT id,url FROM connections'):
                        if row['id'] in RETIRED_IDS or retired_origin(row['url']):
                            raise ImportRefused('Source contains retired connections; no data was imported. Preserve the backup and review retirement cleanup before retrying')
                for name,table in targets.items():
                    batch=[]
                    for row in raw.execute('SELECT * FROM '+name+' ORDER BY rowid'):
                        values={'tenant_id':registry_id}
                        for key,value in dict(row).items():
                            if isinstance(table.c[key].type,sa.JSON) and value is not None:value=json.loads(value)
                            if isinstance(table.c[key].type,sa.DateTime) and value is not None:
                                value=datetime.datetime.fromisoformat(value)
                                if value.tzinfo is None:raise ImportRefused('Legacy timestamps must include a timezone')
                            values[key]=value
                        batch.append(values)
                        if len(batch)==500:insert_rows(db,table,batch);batch=[]
                    if batch:insert_rows(db,table,batch)
                    actual=db.execute(sa.select(sa.func.count()).select_from(table).where(table.c.tenant_id==registry_id)).scalar_one()
                    if actual!=counts[name]:raise ImportRefused('Import row count mismatch for '+name)
                # Product events have no FK because history outlives inventory. The
                # federated schema validates links through its composite FKs.
                for name in ('registry_events','events','sync_runs'):
                    if name in names:advance_sequence(db,'catproof_'+name)
                db.execute(sa.text('INSERT INTO catproof_migrations(tenant_id,id,applied_at) VALUES(:tenant,:marker,now())'),params)
                return {'status':'imported','format':kind,'counts':counts}
        finally:engine.dispose()


def insert_rows(db,table,batch):
    if table.name=='catproof_events':
        # Preserve product audit IDs despite the destination GENERATED ALWAYS identity.
        quote=db.dialect.identifier_preparer.quote
        columns=list(batch[0])
        expressions=['CAST(:'+key+' AS JSONB)' if isinstance(table.c[key].type,sa.JSON) else ':'+key for key in columns]
        statement=sa.text('INSERT INTO '+quote(table.name)+' ('+','.join(map(quote,columns))+') OVERRIDING SYSTEM VALUE VALUES ('+','.join(expressions)+')')
        data=[{key:json.dumps(value) if isinstance(table.c[key].type,sa.JSON) and value is not None else value for key,value in row.items()} for row in batch]
        db.execute(statement,data)
    else:db.execute(table.insert(),batch)


def advance_sequence(db,table):
    sequence=db.execute(sa.text('SELECT pg_get_serial_sequence(:table, :column)'),{'table':table,'column':'sequence'}).scalar_one()
    # Sequence changes are not transactional: only advance, never rewind even when
    # another registry has already allocated IDs above the imported audit history.
    schema,name=db.execute(sa.text('SELECT n.nspname,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.oid=CAST(:sequence AS regclass)'),{'sequence':sequence}).one()
    quote=db.dialect.identifier_preparer.quote
    current=db.exec_driver_sql('SELECT last_value FROM '+quote(schema)+'.'+quote(name)).scalar_one()
    maximum=db.exec_driver_sql('SELECT COALESCE(MAX(sequence),0) FROM '+table).scalar_one()
    db.execute(sa.text('SELECT setval(CAST(:sequence AS regclass),:value,true)'),{'sequence':sequence,'value':max(current,maximum)})
