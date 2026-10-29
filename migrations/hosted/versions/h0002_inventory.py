"""Move inventory into tenant-scoped PostgreSQL tables and retire SQLite blobs."""
from alembic import op
import sqlalchemy as sa
revision='h0002'
down_revision='h0001'
branch_labels=None
depends_on=None
TABLES = ['catproof_connections', 'catproof_entities', 'catproof_observations', 'catproof_bindings', 'catproof_registry_events', 'catproof_migrations', 'catproof_sync_runs', 'catproof_assets', 'catproof_asset_links']
DDL = ['CREATE TABLE catproof_connections (tenant_id TEXT NOT NULL DEFAULT '
 "current_setting('catproof.registry_id',true) REFERENCES catproof_workspaces(id), id TEXT NOT NULL, PRIMARY "
 'KEY(tenant_id,id),name TEXT NOT NULL,kind TEXT NOT NULL,url TEXT NOT NULL,\n'
 '          credential_prefix TEXT NOT NULL,source_filter TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,\n'
 '          created_at TIMESTAMPTZ NOT NULL,last_attempt TIMESTAMPTZ,last_success TIMESTAMPTZ,error '
 "TEXT,mode TEXT NOT NULL DEFAULT 'not connected', config JSONB NOT NULL DEFAULT '{}'::jsonb, "
 "sync_generation TEXT NOT NULL DEFAULT '', CHECK(enabled "
 'IN (0,1)))',
 'CREATE TABLE catproof_entities (tenant_id TEXT NOT NULL DEFAULT '
 "current_setting('catproof.registry_id',true) REFERENCES catproof_workspaces(id), id TEXT NOT NULL, PRIMARY "
 "KEY(tenant_id,id),name TEXT NOT NULL,purpose TEXT NOT NULL DEFAULT '',business_owner TEXT NOT NULL DEFAULT "
 "'',\n"
 "          lifecycle TEXT NOT NULL DEFAULT 'draft',review TEXT NOT NULL DEFAULT 'unreviewed',notes TEXT NOT "
 "NULL DEFAULT '',\n"
 '          created_at TIMESTAMPTZ NOT NULL,updated_at TIMESTAMPTZ NOT NULL,reviewed_at TIMESTAMPTZ,manual '
 'INTEGER NOT NULL DEFAULT 0,archived INTEGER NOT NULL DEFAULT 0, CHECK(lifecycle IN '
 "('draft','active','suspended','retired')), CHECK(review IN ('unreviewed','reviewed')), CHECK(manual IN "
 '(0,1,2)), CHECK(archived IN (0,1)))',
 'CREATE TABLE catproof_observations (tenant_id TEXT NOT NULL DEFAULT '
 "current_setting('catproof.registry_id',true) REFERENCES catproof_workspaces(id), id TEXT NOT NULL, PRIMARY "
 'KEY(tenant_id,id),connection_id TEXT NOT NULL, FOREIGN KEY(tenant_id,connection_id) REFERENCES '
 'catproof_connections(tenant_id,id),payload JSONB NOT NULL,\n'
 '          first_seen TIMESTAMPTZ NOT NULL,present INTEGER NOT NULL DEFAULT 1,stale INTEGER NOT NULL '
 'DEFAULT 0, CHECK(present IN (0,1)), CHECK(stale IN (0,1)))',
 'CREATE TABLE catproof_bindings (tenant_id TEXT NOT NULL DEFAULT '
 "current_setting('catproof.registry_id',true) REFERENCES catproof_workspaces(id), observation_id TEXT NOT "
 'NULL, PRIMARY KEY(tenant_id,observation_id), FOREIGN KEY(tenant_id,observation_id) REFERENCES '
 'catproof_observations(tenant_id,id),entity_id TEXT NOT NULL, FOREIGN KEY(tenant_id,entity_id) REFERENCES '
 'catproof_entities(tenant_id,id))',
 'CREATE TABLE catproof_registry_events (tenant_id TEXT NOT NULL DEFAULT '
 "current_setting('catproof.registry_id',true) REFERENCES catproof_workspaces(id), sequence BIGINT GENERATED "
 'BY DEFAULT AS IDENTITY, PRIMARY KEY(tenant_id,sequence),entity_id TEXT,connection_id TEXT,at TIMESTAMPTZ '
 'NOT NULL,\n'
 "          kind TEXT NOT NULL,name TEXT NOT NULL,detail JSONB NOT NULL, actor TEXT NOT NULL DEFAULT '')",
 'CREATE TABLE catproof_migrations (tenant_id TEXT NOT NULL DEFAULT '
 "current_setting('catproof.registry_id',true) REFERENCES catproof_workspaces(id), id TEXT NOT NULL, PRIMARY "
 'KEY(tenant_id,id), applied_at TIMESTAMPTZ NOT NULL)',
 'CREATE TABLE catproof_sync_runs (tenant_id TEXT NOT NULL DEFAULT '
 "current_setting('catproof.registry_id',true) REFERENCES catproof_workspaces(id), id TEXT NOT NULL, PRIMARY "
 'KEY(tenant_id,id),connection_id TEXT NOT NULL, FOREIGN KEY(tenant_id,connection_id) REFERENCES '
 'catproof_connections(tenant_id,id),started_at TIMESTAMPTZ NOT NULL,\n'
 '          completed_at TIMESTAMPTZ NOT NULL,outcome TEXT NOT NULL,observed INTEGER NOT NULL,error TEXT, '
 "sequence BIGINT GENERATED ALWAYS AS IDENTITY, CHECK(outcome IN ('succeeded','failed')), "
 'CHECK(observed>=0))',
 "CREATE TABLE catproof_assets (tenant_id TEXT NOT NULL DEFAULT current_setting('catproof.registry_id',true) "
 'REFERENCES catproof_workspaces(id), id TEXT NOT NULL, PRIMARY KEY(tenant_id,id), connection_id TEXT NOT '
 'NULL, FOREIGN KEY(tenant_id,connection_id) REFERENCES catproof_connections(tenant_id,id),payload JSONB NOT '
 'NULL,first_seen TIMESTAMPTZ NOT NULL,present INTEGER NOT NULL DEFAULT 1,stale INTEGER NOT NULL DEFAULT 0, '
 'CHECK(present IN (0,1)), CHECK(stale IN (0,1)))',
 'CREATE TABLE catproof_asset_links (tenant_id TEXT NOT NULL DEFAULT '
 "current_setting('catproof.registry_id',true) REFERENCES catproof_workspaces(id), asset_id TEXT NOT NULL, "
 'FOREIGN KEY(tenant_id,asset_id) REFERENCES catproof_assets(tenant_id,id),entity_id TEXT NOT NULL, FOREIGN '
 'KEY(tenant_id,entity_id) REFERENCES catproof_entities(tenant_id,id),PRIMARY '
 'KEY(tenant_id,asset_id,entity_id))']

# Older local UI remains supported on the same PostgreSQL database.
TABLES += ['catproof_agents','catproof_events','catproof_metadata']
DDL += [
    "CREATE TABLE catproof_agents (tenant_id TEXT NOT NULL DEFAULT current_setting('catproof.registry_id',true) REFERENCES catproof_workspaces(id), registry_id TEXT NOT NULL, namespace TEXT NOT NULL, vendor_id TEXT NOT NULL, payload JSONB NOT NULL, first_seen TIMESTAMPTZ NOT NULL, present INTEGER NOT NULL CHECK(present IN (0,1)), business_owner TEXT NOT NULL DEFAULT '', review TEXT NOT NULL DEFAULT 'unreviewed' CHECK(review IN ('unreviewed','reviewed','retired')), notes TEXT NOT NULL DEFAULT '', reviewed_at TIMESTAMPTZ, PRIMARY KEY(tenant_id,registry_id))",
    "CREATE TABLE catproof_events (tenant_id TEXT NOT NULL DEFAULT current_setting('catproof.registry_id',true) REFERENCES catproof_workspaces(id), sequence BIGINT GENERATED ALWAYS AS IDENTITY, namespace TEXT NOT NULL, registry_id TEXT NOT NULL, at TIMESTAMPTZ NOT NULL, kind TEXT NOT NULL, name TEXT NOT NULL, detail JSONB NOT NULL, PRIMARY KEY(tenant_id,sequence))",
    "CREATE TABLE catproof_metadata (tenant_id TEXT NOT NULL DEFAULT current_setting('catproof.registry_id',true) REFERENCES catproof_workspaces(id), namespace TEXT NOT NULL, sources JSONB NOT NULL, synced_at TIMESTAMPTZ NOT NULL, PRIMARY KEY(tenant_id,namespace))",
]

def upgrade():
    bind=op.get_bind()
    for statement in DDL:op.execute(statement)
    from legacy_import import import_snapshots
    import_snapshots(bind)
    # Credentials now have a real connection FK in addition to registry scope.
    op.create_foreign_key('fk_secret_connection','catproof_connector_secrets','catproof_connections',['registry_id','connection_id'],['tenant_id','id'])
    for table,column,target,name in [
        ('catproof_session_meta','registry_id','catproof_workspaces','fk_session_registry'),
        ('catproof_security_events','registry_id','catproof_workspaces','fk_security_registry'),
        ('catproof_invites','created_by','catproof_users','fk_invite_creator'),
        ('catproof_local_pairings','creator','catproof_users','fk_pairing_creator')]:
        op.create_foreign_key(name,table,target,[column],['id'])
    op.create_check_constraint('ck_invite_role','catproof_invites',"role IN ('editor','viewer')")
    op.create_check_constraint('ck_device_sequence','catproof_local_devices','sequence>=0')
    indexes={
        'ix_observations_connection':('catproof_observations',['tenant_id','connection_id']),
        'ix_assets_connection':('catproof_assets',['tenant_id','connection_id']),
        'ix_bindings_entity':('catproof_bindings',['tenant_id','entity_id']),
        'ix_asset_links_entity':('catproof_asset_links',['tenant_id','entity_id']),
        'ix_events_entity':('catproof_registry_events',['tenant_id','entity_id','sequence']),
        'ix_events_connection':('catproof_registry_events',['tenant_id','connection_id','sequence']),
        'ix_sync_connection':('catproof_sync_runs',['tenant_id','connection_id','started_at']),
        'ix_members_user':('catproof_members',['user_id','registry_id']),
        'ix_sessions_user':('catproof_sessions',['user_id']),
        'ix_sessions_expiry':('catproof_sessions',['expires_at']),
        'ix_invites_registry':('catproof_invites',['registry_id','email','expires_at']),
        'ix_peers_registry':('catproof_peer_tokens',['registry_id']),
        'ix_devices_registry':('catproof_local_devices',['registry_id']),
        'ix_pairings_registry':('catproof_local_pairings',['registry_id']),
    }
    for name,(table,columns) in indexes.items():op.create_index(name,table,columns)
    # A NOLOGIN role prevents even a superuser deployment connection from
    # accidentally bypassing RLS during application inventory operations.
    op.execute("DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='catproof_inventory') THEN CREATE ROLE catproof_inventory NOLOGIN NOSUPERUSER NOBYPASSRLS; END IF; END $$")
    role=bind.execute(sa.text("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname='catproof_inventory'")).one()
    if any(role):raise RuntimeError('catproof_inventory must not bypass row security')
    op.execute('GRANT catproof_inventory TO CURRENT_USER')
    schema=bind.execute(sa.text('SELECT current_schema()')).scalar_one()
    quoted=bind.dialect.identifier_preparer.quote(schema)
    op.execute('GRANT USAGE ON SCHEMA '+quoted+' TO catproof_inventory')
    # Existing app authorization covers account operations; inventory additionally
    # gets database-enforced tenant isolation.
    account_tables=['catproof_workspaces','catproof_users','catproof_sessions','catproof_members','catproof_member_initializations','catproof_session_meta','catproof_invites','catproof_security_events','catproof_auth_limits','catproof_peer_tokens','catproof_local_pairings','catproof_local_devices','catproof_connector_secrets']
    for table in account_tables+TABLES:
        op.execute('GRANT SELECT,INSERT,UPDATE,DELETE ON '+quoted+'.'+table+' TO catproof_inventory')
    for table in ('catproof_registry_events','catproof_sync_runs','catproof_events'):
        sequence=bind.execute(sa.text('SELECT pg_get_serial_sequence(:table, :column)'),dict(table=table,column='sequence')).scalar_one()
        op.execute('GRANT USAGE,SELECT ON SEQUENCE '+sequence+' TO catproof_inventory')
    for table in TABLES:
        op.execute('ALTER TABLE '+table+' ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE '+table+' FORCE ROW LEVEL SECURITY')
        op.execute("CREATE POLICY registry_scope ON "+table+" USING (tenant_id=current_setting('catproof.registry_id',true)) WITH CHECK (tenant_id=current_setting('catproof.registry_id',true))")
    # Import and validation above happen in this same migration transaction.
    op.drop_table('catproof_workspace_state')
    op.drop_table('catproof_registry_state')

def downgrade():
    raise RuntimeError('This data migration is forward-only. Restore the pre-upgrade backup with the old application.')
