"""Keep public website leads separate from account and tenant inventory data."""
from alembic import op

revision='h0007'
down_revision='h0006'
branch_labels=None
depends_on=None

def upgrade():
    op.execute('''CREATE TABLE catproof_website_leads (
        email TEXT NOT NULL CHECK (length(email) BETWEEN 3 AND 254),
        kind TEXT NOT NULL CHECK (kind IN ('updates','contact')),
        name TEXT NOT NULL DEFAULT '' CHECK (length(name)<=120),
        company TEXT NOT NULL DEFAULT '' CHECK (length(company)<=200),
        platforms TEXT NOT NULL DEFAULT '' CHECK (length(platforms)<=500),
        message TEXT NOT NULL DEFAULT '' CHECK (length(message)<=5000),
        policy_version TEXT NOT NULL,
        consented_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(email,kind)
    )''')
    op.execute('REVOKE ALL ON catproof_website_leads FROM PUBLIC')

def downgrade():
    raise RuntimeError('Preserve captured enquiries and consent records; restore a reviewed backup if needed.')
