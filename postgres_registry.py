"""Transaction adapter for the registry's parameterized PostgreSQL statements.

Tenant visibility is enforced by PostgreSQL RLS under a dedicated non-login role,
including when the deployment owner is a superuser. Composite foreign keys also
prevent cross-registry relationships. No SQL statement is rewritten for scoping.
"""
import json
import datetime

class Row(dict):
    def __getitem__(self,key):
        return tuple(self.values())[key] if isinstance(key,int) else super().__getitem__(key)

def row_factory(cursor):
    names=[column.name for column in (cursor.description or [])]
    def row(values):
        return Row(zip(names,[json.dumps(v,sort_keys=True) if isinstance(v,(dict,list)) else v.isoformat() if isinstance(v,(datetime.datetime,datetime.date)) else v for v in values]))
    return row

class RegistryConnection:
    def __init__(self,connection,workspace_id,owned=False):
        self.raw=connection;self.owned=owned;self.transactions=[]
        # On hosted requests these settings are local to the outer transaction.
        # A standalone registry owns its connection and uses session settings.
        connection.execute('SET '+('' if owned else 'LOCAL ')+'ROLE catproof_inventory')
        connection.execute('SELECT set_config(%s,%s,%s)',('catproof.registry_id',workspace_id,not owned))
    def execute(self,statement,args=()):
        return self.raw.cursor(row_factory=row_factory).execute(statement.replace('?','%s'),args)
    def __enter__(self):
        context=self.raw.transaction();context.__enter__();self.transactions.append(context);return self
    def __exit__(self,*args):return self.transactions.pop().__exit__(*args)
    def commit(self):
        if self.owned:self.raw.commit()
    def close(self):
        if self.owned:self.raw.close()
