"""Require canonical bindings and index the latest tenant sync history."""
from alembic import op

revision='h0005'
down_revision='h0004'
branch_labels=None
depends_on=None


def upgrade():
    # Together with the binding PK and forward FK, this requires exactly one
    # binding per observation. Defer the reverse edge so normal ingest can insert
    # the observation before its binding, in one transaction. Existing orphans
    # fail validation; never invent canonical decisions to repair them silently.
    op.create_foreign_key('fk_observation_required_binding',
                          'catproof_observations','catproof_bindings',
                          ['tenant_id','id'],['tenant_id','observation_id'],
                          deferrable=True,initially='DEFERRED')
    op.execute('CREATE INDEX ix_sync_latest ON catproof_sync_runs '
               '(tenant_id, completed_at DESC, sequence DESC)')


def downgrade():
    raise RuntimeError('Restore a reviewed backup instead of removing inventory integrity constraints.')
