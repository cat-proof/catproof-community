"""Create local credentials once. Never print or overwrite secret values."""
import argparse
import base64
import os
from pathlib import Path
import secrets


def initialize(path):
    path = Path(path)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        print('Existing .env preserved. No credentials were changed.')
        return False
    try:
        with os.fdopen(fd, 'w') as output:
            output.write('# CatProof Community Edition: keep private and back up securely.\n')
            output.write('POSTGRES_PASSWORD=' + secrets.token_hex(32) + '\n')
            output.write('APP_BOOTSTRAP_PASSWORD=' + secrets.token_hex(32) + '\n')
            output.write('CONNECTOR_ENCRYPTION_KEYS=' + base64.urlsafe_b64encode(secrets.token_bytes(32)).decode() + '\n')
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    print('Created private local configuration. Keep .env with your backups.')
    return True


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--path', default='/workspace/.env')
    initialize(parser.parse_args().path)
