"""Adopt main's password recovery table without rearming existing grants."""
from alembic import op

revision='h0004'
down_revision='h0003'
branch_labels=None
depends_on=None


def upgrade():
    # Main previously created this table at startup. Keep existing hashes,
    # absolute expiries and used_at values when adopting such installations.
    op.execute('CREATE TABLE IF NOT EXISTS catproof_account_recovery ('
               'token_hash TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES catproof_users(id),'
               'expires_at DOUBLE PRECISION NOT NULL,used_at DOUBLE PRECISION)')
    op.create_index('ix_account_recovery_user','catproof_account_recovery',['user_id'])
    op.create_index('ix_account_recovery_expiry','catproof_account_recovery',['expires_at'])


def downgrade():
    raise RuntimeError('Recovery grants must not be reset by downgrade; restore the reviewed backup instead.')
