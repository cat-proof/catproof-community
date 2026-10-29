"""Killable connector reads: credentials travel on stdin, never command arguments."""
import json
import os
from pathlib import Path
import subprocess
import sys
from registry import ConnectorError


def fetch_snapshot(connection, environment, timeout=30):
    # Do not inherit application secrets, proxy settings or SDK endpoint overrides.
    worker_env = {k: os.environ[k] for k in ('PATH', 'HOME', 'SYSTEMROOT', 'SSL_CERT_FILE') if k in os.environ}
    try:
        result = subprocess.run([sys.executable, str(Path(__file__).resolve())],
            input=json.dumps({'connection': connection, 'environment': environment}),
            capture_output=True, text=True, timeout=timeout, env=worker_env, check=False)
    except subprocess.TimeoutExpired:
        raise ConnectorError('Sync deadline exceeded; previous observations retained.') from None
    if result.returncode or len(result.stdout) > 20_000_000:
        raise ConnectorError('Connector worker failed or exceeded its response limit.')
    try:
        output = json.loads(result.stdout)
        if 'error' in output:
            raise ConnectorError(output['error'])
        return output['payload']
    except (ValueError, KeyError, TypeError):
        raise ConnectorError('Connector worker returned an invalid response.') from None


def main():
    from federation import discover
    from network_policy import hosted_egress
    try:
        data = json.load(sys.stdin)
        with hosted_egress():
            payload = discover(data['connection'], data['environment'], True)
        output = json.dumps({'payload': payload})
        if len(output) > 20_000_000:
            raise ConnectorError('Connector response exceeds the sync size limit.')
    except Exception as exc:
        output = json.dumps({'error': str(exc) if isinstance(exc, ConnectorError)
                             else 'Connector read failed; previous observations retained.'})
    print(output)


if __name__ == '__main__':
    main()
