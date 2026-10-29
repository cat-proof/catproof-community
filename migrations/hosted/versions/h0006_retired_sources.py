"""Apply upstream retired-source cleanup to relational PostgreSQL inventory."""
import json
from alembic import op
import sqlalchemy as sa
from postgres_registry import RegistryConnection
from registry_locks import lock_registry
from retired_sources import purge_retired

revision='h0006'
down_revision='h0005'
branch_labels=None
depends_on=None


def upgrade():
    bind=op.get_bind()
    # Adopt main's receipt table if its snapshot cleanup already ran. Keep that
    # historical receipt and record this relational migration under a new ID.
    op.execute('CREATE TABLE IF NOT EXISTS catproof_data_migrations '
               '(id TEXT PRIMARY KEY,receipt TEXT NOT NULL)')
    totals={'registries_checked':0,'connections_removed':0,'entities_removed':0,'credentials_removed':0}
    tenants=bind.exec_driver_sql('SELECT id FROM catproof_workspaces ORDER BY id').scalars().all()
    owner=bind.exec_driver_sql('SELECT current_user').scalar_one()
    quote=bind.dialect.identifier_preparer.quote
    raw=bind.connection.driver_connection
    for tenant in tenants:
        lock_registry(raw,tenant)
        registry=RegistryConnection(raw,tenant)
        result=purge_retired(registry)
        totals['registries_checked']+=1
        totals['connections_removed']+=len(result['connections'])
        totals['entities_removed']+=result['entities_removed']
        totals['credentials_removed']+=result['credentials_removed']
        bind.exec_driver_sql('SET LOCAL ROLE '+quote(owner))
    bind.execute(sa.text('INSERT INTO catproof_data_migrations(id,receipt) VALUES(:id,:receipt)'),
                 {'id':'retired-source-postgres-v1','receipt':json.dumps(totals)})


def downgrade():
    raise RuntimeError('Retired inventory must not be recreated; restore a reviewed backup if required.')
