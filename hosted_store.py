"""PostgreSQL registry storage with short per-registry write transactions.

Provider fetches run outside transactions; inventory, credentials and audit writes
share the same relational database and commit boundary.
"""
from contextlib import contextmanager
import os
import re
from federation import FederatedRegistry
from registry_locks import lock_registry


def credential_environment(workspace_id):
    """Never expose process/application secrets to a connection's prefix selector."""
    if workspace_id != 'legacy':
        prefix = 'CATPROOF_' + workspace_id.upper() + '_'
        return {k[len(prefix):]: v for k, v in os.environ.items() if k.startswith(prefix)}
    prefixes = {p.strip() for p in os.getenv('CATPROOF_LEGACY_CREDENTIAL_PREFIXES', '').split(',')}
    suffixes = ('TOKEN', 'USERNAME', 'PASSWORD', 'OWNER_FIELD', 'PROFILE',
                'ACCESS_KEY_ID', 'SECRET_ACCESS_KEY', 'SESSION_TOKEN')
    allowed = {p + '_' + suffix for p in prefixes
               if re.fullmatch('[A-Z][A-Z0-9_]{0,79}', p) and not p.startswith('CATPROOF_')
               for suffix in suffixes}
    return {k: v for k, v in os.environ.items() if k in allowed}

class HostedStore:
    def __init__(self, database_url, connect=None):
        if connect is None:
            import psycopg
            connect=psycopg.connect
        self.connect=connect;self.url=database_url
        self.store=FederatedRegistry(self.url,connect=self.connect,env={})
        from database import require_hosted_schema
        with self.connect(self.url,connect_timeout=5) as db:require_hosted_schema(db)
        with self.request():pass

    @contextmanager
    def request(self,write=False,workspace_id='legacy'):
        with self.connect(self.url,connect_timeout=5) as db:
            if write:lock_registry(db, workspace_id)
            else:db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            if not db.execute('SELECT 1 FROM catproof_workspaces WHERE id=%s',(workspace_id,)).fetchone():
                raise RuntimeError('Registry is unavailable')
            registry=FederatedRegistry(env=credential_environment(workspace_id),workspace_id=workspace_id,db=db)
            registry.credentials=self.credentials.load(db,workspace_id) if hasattr(self,'credentials') else {}
            try:yield registry
            finally:registry.credentials={}

    def refresh(self, workspace_id, connection_id, authorize, fetch=None):
        """Start each attempted generation under lock, fetch, then reauthorize and apply."""
        import time
        import uuid
        from registry import ConnectorError, now
        from sync_worker import fetch_snapshot
        fetch = fetch or fetch_snapshot
        deadline = time.monotonic() + 60
        with self.request(False, workspace_id) as registry:
            authorize()
            if connection_id:registry.require_connection_available(connection_id)
            available=set(registry.available_connection_ids())
            jobs = [dict(c) for c in registry.db.execute(
                'SELECT * FROM catproof_connections WHERE enabled=1' + (' AND id=?' if connection_id else ''),
                (connection_id,) if connection_id else ())]
            jobs=[job for job in jobs if job['id'] in available]
            jobs.sort(key=lambda job:(job['last_attempt'] or '',job['id']))
            if connection_id and not jobs:
                raise ValueError('Connection is missing or paused')
            environments = {job['id']: registry.credential_env(job) for job in jobs}
        for job in jobs:
            if deadline <= time.monotonic():break
            with self.request(True, workspace_id) as registry:
                authorize()
                current=registry.db.execute('SELECT * FROM catproof_connections WHERE id=?',(job['id'],)).fetchone()
                if not current or not current['enabled'] or current['sync_generation']!=job['sync_generation']:continue
                if job['id'] not in registry.available_connection_ids():continue
                remaining=deadline-time.monotonic()
                if remaining<=0:break
                job['last_attempt']=now()
                job['sync_generation']=uuid.uuid4().hex
                registry.db.execute('UPDATE catproof_connections SET last_attempt=?,sync_generation=? WHERE id=?',
                                    (job['last_attempt'],job['sync_generation'],job['id']))
            payload = None
            error = None
            try:
                payload = fetch(job, environments.pop(job['id']), min(30, remaining))
            except Exception as exc:
                error = str(exc) if isinstance(exc, ConnectorError) else 'Connector failed; previous observations retained.'
            with self.request(True, workspace_id) as registry:
                actor = authorize()
                current = registry.db.execute('SELECT * FROM catproof_connections WHERE id=?', (job['id'],)).fetchone()
                if not current or not current['enabled'] or current['sync_generation'] != job['sync_generation']:
                    continue  # A newer sync or pause invalidated this result.
                if job['id'] not in registry.available_connection_ids():continue
                registry.actor = actor['email']
                registry.apply_refresh(job, payload, error, job['last_attempt'])
        with self.request(False, workspace_id) as registry:
            authorize()
            return registry.snapshot()

    def preview(self, workspace_id, connection_id, authorize, fetch=None):
        """Read a bounded source preview without mutating inventory or sync state."""
        from sync_worker import fetch_snapshot
        from federation import validate_rows, validate_assets
        with self.request(False, workspace_id) as registry:
            authorize()
            row = registry.db.execute('SELECT * FROM catproof_connections WHERE id=? AND enabled=1', (connection_id,)).fetchone()
            if not row:
                raise ValueError('Connection is missing or paused')
            registry.require_connection_available(connection_id)
            job = dict(row)
            environment = registry.credential_env(job)
        payload = (fetch or fetch_snapshot)(job, environment, 30)
        rows = validate_rows(payload.get('agents'))
        assets = validate_assets(payload.get('assets', []))
        if len(rows) + len(assets) > 10000:
            raise ValueError('Inventory exceeds the 10,000 record limit')
        with self.request(False, workspace_id) as registry:
            authorize()
            registry.require_connection_available(connection_id)
            current = registry.db.execute('SELECT * FROM catproof_connections WHERE id=?', (connection_id,)).fetchone()
            if not current or not current['enabled'] or current['sync_generation'] != job['sync_generation']:
                raise ValueError('Connection changed during preview. Preview it again.')
        return {'connection_id': connection_id, 'agent_count': len(rows), 'asset_count': len(assets),
                'agents': [{k: r.get(k, '') for k in ('name', 'source_id', 'id', 'source', 'status', 'provenance')} for r in rows[:50]],
                'assets': [{k: r.get(k, '') for k in ('name', 'resource_type', 'provenance')} for r in assets[:20]],
                'note': 'Preview only; no inventory changed. Import reads the latest source again, so counts may change.'}

    def healthy(self):
        with self.connect(self.url,connect_timeout=3) as db:
            return bool(db.execute("SELECT 1 FROM catproof_workspaces WHERE id='legacy'").fetchone())

    def remove_retired_sources(self):
        """Report the count-only Alembic cleanup receipt; startup performs no DDL."""
        import json
        with self.connect(self.url,connect_timeout=5) as db:
            row=db.execute('SELECT receipt FROM catproof_data_migrations WHERE id=%s',
                           ('retired-source-postgres-v1',)).fetchone()
            if not row:raise RuntimeError('Retired-source migration missing; run python database.py upgrade')
            return json.loads(row[0])
