"""Preserve connection evidence when releasing a Community Edition slot."""
from alembic import op

revision = 'h0008'
down_revision = 'h0007'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('ALTER TABLE catproof_connections ADD COLUMN archived INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0,1))')
    op.execute('ALTER TABLE catproof_connections ADD CONSTRAINT catproof_archived_connection_paused CHECK (archived=0 OR enabled=0)')


def downgrade():
    raise RuntimeError('Restore the matching database backup and application version to roll back connection archiving.')
