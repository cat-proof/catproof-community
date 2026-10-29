"""Explicit PostgreSQL initialization, adoption and Alembic schema migrations.

Application constructors check hosted revisions; only the deployment entrypoint or
an operator runs upgrades. Inventory is relational PostgreSQL data.
"""
import argparse
from pathlib import Path
import os
import threading

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
import sqlalchemy as sa
from sqlalchemy.pool import NullPool

ROOT=Path(__file__).resolve().parent
# Alembic's operation proxy is process-global. Serialize in-process migration calls.
MIGRATION_LOCK=threading.RLock()
VERSION_TABLE='catproof_alembic_version'

def adopt_version_table(connection):
    """Called only by online Alembic operations under the migration lock.

    Old CatProof releases used the default table. Rename only recognized history
    accompanied by CatProof's account tables; foreign applications keep theirs.
    """
    inspector=sa.inspect(connection)
    schema=connection.exec_driver_sql('SELECT current_schema()').scalar_one()
    tables=set(inspector.get_table_names(schema=schema))
    if VERSION_TABLE in tables or 'alembic_version' not in tables:return
    if not {'catproof_users','catproof_workspaces'}<=tables:return
    quote=connection.dialect.identifier_preparer.quote
    old=quote(schema)+'.alembic_version'
    revisions=connection.exec_driver_sql('SELECT version_num FROM '+old).scalars().all()
    # These are the only revisions shipped with generic Alembic metadata.
    if len(revisions)==1 and revisions[0] in {'h0001','h0002','h0003','h0004'}:
        connection.exec_driver_sql('ALTER TABLE '+old+' RENAME TO '+VERSION_TABLE)
    elif 'catproof_connections' in tables or any(r.startswith('h') for r in revisions):
        raise RuntimeError('Unrecognized legacy database revision; verify CatProof migration history before upgrading')

def configuration(kind,connection=None):
    cfg=Config(str(ROOT/'alembic.ini'),ini_section=kind)
    if connection is not None:cfg.attributes['connection']=connection
    return cfg

def head(kind):
    return ScriptDirectory.from_config(configuration(kind)).get_current_head()

HOSTED_HEAD=head('hosted')
def require_hosted_schema(db):
    try:row=db.execute('SELECT version_num FROM '+VERSION_TABLE).fetchall()
    except Exception:raise RuntimeError('Database is not initialized; run python database.py upgrade before starting the app') from None
    if [r[0] for r in row]!=[HOSTED_HEAD]:
        raise RuntimeError('Database revision does not match this application; run python database.py upgrade')

def engine_for(url=None,creator=None):
    url=url or os.environ.get('DATABASE_URL','')
    if not url:raise ValueError('Set DATABASE_URL')
    if url.startswith('postgres://'):url='postgresql+psycopg://'+url[len('postgres://'):]
    elif url.startswith('postgresql://'):url='postgresql+psycopg://'+url[len('postgresql://'):]
    if not url.startswith('postgresql+psycopg://'):raise ValueError('Hosted migrations require PostgreSQL')
    return sa.create_engine(url,poolclass=NullPool,hide_parameters=True,**({'creator':creator} if creator else {}))

def upgrade_hosted(url=None,creator=None):
    engine=engine_for(url,creator)
    try:
        with MIGRATION_LOCK,engine.begin() as connection:
            command.upgrade(configuration('hosted',connection),'head')
    finally:engine.dispose()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['upgrade','current','check','import-sqlite'])
    parser.add_argument('source',nargs='?',type=Path,help='Offline standalone SQLite backup for import-sqlite')
    parser.add_argument('--registry-id',help='Existing empty target registry; use legacy for the standalone interfaces')
    args=parser.parse_args()
    if args.action=='import-sqlite':
        if not args.source or not args.registry_id:parser.error('import-sqlite requires a source file and --registry-id')
        from sqlite_import import import_sqlite,ImportRefused
        import json
        try:result=import_sqlite(args.source,args.registry_id)
        except ImportRefused as exc:parser.exit(1,'Import refused: '+str(exc)+'\n')
        except Exception:parser.exit(1,'SQLite import did not complete successfully. Check the offline source, schema, relationships, and target permissions. If the commit outcome is uncertain, retry the same file and registry; completed imports are not applied twice.\n')
        print(json.dumps(result,sort_keys=True))
        return
    if args.source or args.registry_id:parser.error('Source file and --registry-id apply only to import-sqlite')
    if args.action=='upgrade':upgrade_hosted();print('Database upgraded to '+HOSTED_HEAD)
    else:
        engine=engine_for()
        try:
            with engine.connect() as connection:
                if args.action=='current':command.current(configuration('hosted',connection))
                else:
                    rows=connection.exec_driver_sql('SELECT version_num FROM '+VERSION_TABLE).scalars().all()
                    if rows!=[HOSTED_HEAD]:raise RuntimeError('Database upgrade required')
                    print('Database revision matches '+HOSTED_HEAD)
        finally:engine.dispose()

if __name__=='__main__':main()
