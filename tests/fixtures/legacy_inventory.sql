
        CREATE TABLE IF NOT EXISTS connections (
          id TEXT PRIMARY KEY,name TEXT NOT NULL,kind TEXT NOT NULL,url TEXT NOT NULL,
          credential_prefix TEXT NOT NULL,source_filter TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL,last_attempt TEXT,last_success TEXT,error TEXT,mode TEXT NOT NULL DEFAULT 'not connected');
        CREATE TABLE IF NOT EXISTS entities (
          id TEXT PRIMARY KEY,name TEXT NOT NULL,purpose TEXT NOT NULL DEFAULT '',business_owner TEXT NOT NULL DEFAULT '',
          lifecycle TEXT NOT NULL DEFAULT 'draft',review TEXT NOT NULL DEFAULT 'unreviewed',notes TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,updated_at TEXT NOT NULL,reviewed_at TEXT,manual INTEGER NOT NULL DEFAULT 0,archived INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS observations (
          id TEXT PRIMARY KEY,connection_id TEXT NOT NULL REFERENCES connections(id),payload TEXT NOT NULL,
          first_seen TEXT NOT NULL,present INTEGER NOT NULL DEFAULT 1,stale INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS bindings (
          observation_id TEXT PRIMARY KEY REFERENCES observations(id),entity_id TEXT NOT NULL REFERENCES entities(id));
        CREATE TABLE IF NOT EXISTS registry_events (
          sequence INTEGER PRIMARY KEY AUTOINCREMENT,entity_id TEXT,connection_id TEXT,at TEXT NOT NULL,
          kind TEXT NOT NULL,name TEXT NOT NULL,detail TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS migrations (id TEXT PRIMARY KEY, applied_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sync_runs (
          id TEXT PRIMARY KEY,connection_id TEXT NOT NULL REFERENCES connections(id),started_at TEXT NOT NULL,
          completed_at TEXT NOT NULL,outcome TEXT NOT NULL,observed INTEGER NOT NULL,error TEXT);


        CREATE TABLE IF NOT EXISTS assets (id TEXT PRIMARY KEY, connection_id TEXT NOT NULL REFERENCES connections(id),payload TEXT NOT NULL,first_seen TEXT NOT NULL,present INTEGER NOT NULL DEFAULT 1,stale INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS asset_links (asset_id TEXT NOT NULL REFERENCES assets(id),entity_id TEXT NOT NULL REFERENCES entities(id),PRIMARY KEY(asset_id,entity_id));

ALTER TABLE connections ADD COLUMN config TEXT NOT NULL DEFAULT '{}';
ALTER TABLE connections ADD COLUMN sync_generation TEXT NOT NULL DEFAULT '';
ALTER TABLE registry_events ADD COLUMN actor TEXT NOT NULL DEFAULT '';
