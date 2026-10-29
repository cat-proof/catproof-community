"""Read-only connector catalog. Hosted credentials are decrypted only for their registry and connection."""
from .catalog import CATALOG, validate_config
from .readers import read_connector
