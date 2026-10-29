"""Index large connector configurations using a fixed-size SHA-256 digest."""
from alembic import op
import sqlalchemy as sa
revision='h0003'
down_revision='h0002'
branch_labels=None
depends_on=None

def upgrade():
    # The early local h0002 installation had a direct JSONB unique constraint.
    # New installs defer uniqueness to this revision so legacy large scopes can
    # be imported before validating uniqueness, all in the same transaction.
    for constraint in sa.inspect(op.get_bind()).get_unique_constraints('catproof_connections'):
        if constraint['column_names']==['tenant_id','kind','url','source_filter','config']:
            op.drop_constraint(constraint['name'],'catproof_connections',type_='unique')
    # jsonb_send is immutable and canonical for equal JSONB objects. SHA-256 is
    # built in to PostgreSQL; no extension or extra database role is required.
    op.execute('CREATE UNIQUE INDEX uq_catproof_connection_scope ON catproof_connections '
               '(tenant_id,kind,url,source_filter,(sha256(jsonb_send(config))))')

def downgrade():
    raise RuntimeError('Restoring the old index can reject valid large scopes; use the reviewed backup rollback procedure.')
