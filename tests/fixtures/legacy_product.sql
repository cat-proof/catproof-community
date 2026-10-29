CREATE TABLE agents (
  registry_id TEXT PRIMARY KEY, namespace TEXT NOT NULL, vendor_id TEXT NOT NULL,
  payload TEXT NOT NULL, first_seen TEXT NOT NULL, present INTEGER NOT NULL,
  business_owner TEXT NOT NULL DEFAULT '', review TEXT NOT NULL DEFAULT 'unreviewed',
  notes TEXT NOT NULL DEFAULT '', reviewed_at TEXT);
CREATE TABLE events (
  sequence INTEGER PRIMARY KEY AUTOINCREMENT, namespace TEXT NOT NULL,
  registry_id TEXT NOT NULL, at TEXT NOT NULL, kind TEXT NOT NULL,
  name TEXT NOT NULL, detail TEXT NOT NULL);
CREATE TABLE metadata (namespace TEXT PRIMARY KEY, sources TEXT NOT NULL, synced_at TEXT NOT NULL);
