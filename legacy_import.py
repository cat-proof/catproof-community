"""One-time read-only SQLite blob importer for Alembic h0002.

SQLite is not a runtime database. Any corrupt blob, invalid relation, duplicate
identity, or unsupported data aborts the enclosing PostgreSQL migration.
"""
import datetime
import json
import sqlite3
import sqlalchemy as sa

TABLES=('connections','entities','observations','bindings','registry_events','migrations','sync_runs','assets','asset_links')
PRODUCT_TABLES=('agents','events','metadata')

class ImportRefused(ValueError):
    """An operator-safe validation message containing no source row values."""

def source_layout(raw):
    objects=raw.execute("SELECT name,type FROM sqlite_master WHERE name NOT GLOB 'sqlite_*'").fetchall()
    if any(row['type'] in ('view','trigger') for row in objects):
        raise ImportRefused('Unsupported SQLite views or triggers')
    present={row['name'] for row in objects if row['type']=='table'}
    if {'connections','entities','observations','bindings'}<=present and present<=set(TABLES):
        return 'federated',tuple(name for name in TABLES if name in present)
    if present==set(PRODUCT_TABLES):return 'product',PRODUCT_TABLES
    raise ImportRefused('Unsupported or mixed SQLite schema; no tables were imported')

def validate_source(raw,targets,kind):
    # Inspect schemas before rows: unsupported columns on empty tables must also
    # fail, and the destination tenant must never come from source data.
    for name,table in targets.items():
        columns={row['name'] for row in raw.execute('PRAGMA table_xinfo('+name+')')}
        if columns-set(table.c.keys()) or 'tenant_id' in columns:
            raise ImportRefused('Unsupported columns in SQLite table '+name)
        required={c.name for c in table.c if c.name!='tenant_id' and not c.nullable
                  and c.server_default is None and c.identity is None}
        if required-columns:raise ImportRefused('Missing required columns in SQLite table '+name)
    if raw.execute('PRAGMA foreign_key_check').fetchone():
        raise ImportRefused('SQLite foreign key check failed')
    if kind=='federated' and raw.execute('SELECT 1 FROM observations o LEFT JOIN bindings b ON b.observation_id=o.id WHERE b.observation_id IS NULL LIMIT 1').fetchone():
        raise ImportRefused('Every observation must have a canonical binding')

def import_snapshots(bind):
    snapshots={row.workspace_id:(bytes(row.payload),row.updated_at) for row in bind.execute(sa.text('SELECT workspace_id,payload,updated_at FROM catproof_workspace_state'))}
    legacy=bind.execute(sa.text('SELECT payload,updated_at FROM catproof_registry_state WHERE id=1')).first()
    if legacy and ('legacy' not in snapshots or legacy.updated_at>snapshots['legacy'][1]):snapshots['legacy']=(bytes(legacy.payload),legacy.updated_at)
    metadata=sa.MetaData()
    targets={name:sa.Table('catproof_'+name,metadata,autoload_with=bind) for name in TABLES}
    for tenant,(payload,_) in snapshots.items():
        raw=sqlite3.connect(':memory:');raw.row_factory=sqlite3.Row
        try:
            raw.deserialize(payload);raw.execute('PRAGMA query_only=ON')
            if raw.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Corrupt legacy inventory')
            kind,available=source_layout(raw)
            if kind!='federated':raise ImportRefused('Unsupported legacy inventory schema')
            validate_source(raw,{name:targets[name] for name in available},kind)
            for name,table in targets.items():
                if name not in available:continue
                count=0;batch=[]
                for row in raw.execute('SELECT * FROM '+name):
                    values={'tenant_id':tenant}
                    for key,value in dict(row).items():
                        if key not in table.c:raise ValueError('Unsupported legacy column: '+name+'.'+key)
                        column=table.c[key]
                        if isinstance(column.type,sa.JSON) and value is not None:value=json.loads(value)
                        if isinstance(column.type,sa.DateTime) and value is not None:value=datetime.datetime.fromisoformat(value)
                        values[key]=value
                    batch.append(values);count+=1
                    if len(batch)==500:bind.execute(table.insert(),batch);batch=[]
                if batch:bind.execute(table.insert(),batch)
                actual=bind.execute(sa.select(sa.func.count()).select_from(table).where(table.c.tenant_id==tenant)).scalar_one()
                if actual!=count:raise ValueError('Legacy import count mismatch: '+name)
        except Exception as exc:
            raise RuntimeError('Legacy registry import failed; migration rolled back for '+tenant) from exc
        finally:raw.close()
    # Preserve old per-registry audit sequence values and start new events above
    # every imported sequence, avoiding collisions after a restart.
    bind.execute(sa.text("SELECT setval(pg_get_serial_sequence('catproof_registry_events','sequence'),COALESCE((SELECT MAX(sequence)+1 FROM catproof_registry_events),1),false)"))
