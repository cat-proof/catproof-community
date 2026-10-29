"""Disposable PostgreSQL fixtures shared by unit/HTTP and integration suites."""
import atexit
import os
import threading
import unittest
import uuid
from urllib.parse import urlsplit
import psycopg
from psycopg import sql

_lock=threading.RLock()
_databases={}

def database(label=None):
    url=os.getenv('TEST_POSTGRES_URL','')
    if not url:raise unittest.SkipTest('Set TEST_POSTGRES_URL or run docker compose --profile test run --rm tests')
    if urlsplit(url).path!='/catproof_test':raise RuntimeError('Tests require the disposable catproof_test database')
    key=str(label) if label is not None and str(label)!=':memory:' else uuid.uuid4().hex
    with _lock:
        if key not in _databases:
            schema='test_'+uuid.uuid4().hex
            with psycopg.connect(url) as db:db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
            def connect(*args,**kwargs):return psycopg.connect(url,options='-c search_path='+schema,**kwargs)
            _databases[key]=(url,connect,schema)
            from database import upgrade_hosted
            upgrade_hosted(url,creator=connect)
        url,connect,_=_databases[key]
        return url,connect

def connection(label):return database(label)[1]()

def registry(label=None,**kwargs):
    from federation import FederatedRegistry
    url,connect=database(label)
    return FederatedRegistry(url,connect=connect,**kwargs)

def product_store(label,namespace):
    from product import ProductStore
    url,connect=database(label)
    return ProductStore(url,namespace,connect=connect)

@atexit.register
def cleanup():
    for url,_,schema in _databases.values():
        try:
            with psycopg.connect(url) as db:db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
        except Exception:pass
