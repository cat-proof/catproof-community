"""Use DATABASE_URL or an injected transactional connection; never log its value."""
from alembic import context
from database import engine_for, adopt_version_table, VERSION_TABLE

def run(connection):
    if connection.dialect.name=='postgresql':
        connection.exec_driver_sql("SET LOCAL lock_timeout = '30s'")
        connection.exec_driver_sql('SELECT pg_advisory_xact_lock(7102771)')
    adopt_version_table(connection)
    context.configure(connection=connection,target_metadata=None,transactional_ddl=True,compare_type=True,version_table=VERSION_TABLE)
    with context.begin_transaction():context.run_migrations()

if context.is_offline_mode():
    raise RuntimeError('Adoption migrations require an online database inspection')
connection=context.config.attributes.get('connection')
if connection is not None:run(connection)
else:
    engine=engine_for()
    try:
        with engine.begin() as connection:run(connection)
    finally:engine.dispose()
