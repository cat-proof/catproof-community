"""Stable, database-wide locks shared by registry writes and access changes."""
import hashlib


def lock_registry(db, registry_id):
    key = int.from_bytes(hashlib.sha256(('catproof:registry:' + registry_id).encode()).digest()[:8], 'big', signed=True)
    db.execute("SET LOCAL lock_timeout = '5s'")
    db.execute('SELECT pg_advisory_xact_lock(%s)', (key,))
