"""Prompt locally for live credentials, verify selected sources, then launch the app.
Secrets exist only in this process and the launched server's environment.
"""
import argparse
import getpass
import os
import sys
from pathlib import Path
from registry import Registry


def main(argv=None):
    parser = argparse.ArgumentParser(description='Verify read-only vendor access using hidden local token prompts.')
    parser.add_argument('--source', choices=['both', 'databricks', 'servicenow'], default='both')
    parser.add_argument('--host', help='HTTPS origin for a single selected source')
    parser.add_argument('--product', action='store_true', help='Start persistent product instead of discovery demo')
    parser.add_argument('--servicenow-auth', choices=['bearer', 'basic'], default='bearer')
    parser.add_argument('--check-only', action='store_true', help='Verify access without starting the dashboard')
    args = parser.parse_args(argv)
    if args.host and args.source == 'both':
        parser.error('--host requires one selected source')
    env = dict(os.environ)
    selected = {'Databricks', 'ServiceNow'} if args.source == 'both' else {'Databricks' if args.source == 'databricks' else 'ServiceNow'}
    # Explicit single-source testing must not contact the other vendor.
    for name, keys in [('Databricks', ['DATABRICKS_HOST','DATABRICKS_TOKEN']), ('ServiceNow', ['SERVICENOW_INSTANCE_URL','SERVICENOW_TOKEN','SERVICENOW_USERNAME','SERVICENOW_PASSWORD'])]:
        if name not in selected:
            for key in keys:
                env.pop(key, None)
    if args.host:
        env['DATABRICKS_HOST' if args.source == 'databricks' else 'SERVICENOW_INSTANCE_URL'] = args.host.rstrip('/')
    for label, host_key, token_key in [
        ('Databricks workspace', 'DATABRICKS_HOST', 'DATABRICKS_TOKEN'),
        ('ServiceNow instance', 'SERVICENOW_INSTANCE_URL', 'SERVICENOW_TOKEN'),
    ]:
        if label.split()[0] not in selected:
            continue
        if not env.get(host_key):
            env[host_key] = input(label + ' HTTPS URL: ').strip().rstrip('/')
        if label.startswith('ServiceNow') and args.servicenow_auth == 'basic':
            env.pop('SERVICENOW_TOKEN', None)
            if not env.get('SERVICENOW_USERNAME'):
                env['SERVICENOW_USERNAME'] = input('ServiceNow username: ').strip()
            if not env.get('SERVICENOW_PASSWORD'):
                env['SERVICENOW_PASSWORD'] = getpass.getpass('ServiceNow password (hidden): ')
            if not env[host_key] or not env['SERVICENOW_USERNAME'] or not env['SERVICENOW_PASSWORD']:
                print('URL, username and password are required. No server was started.')
                return 1
            continue
        if not env.get(token_key):
            env[token_key] = getpass.getpass(label + ' bearer token (hidden): ').strip()
        if not env[host_key] or not env[token_key]:
            print('The selected connection requires a URL and token. No server was started.')
            return 1
    print('Checking selected read-only APIs…', flush=True)
    result = Registry(env).refresh()
    failed = False
    for source in result['sources']:
        if source['name'] not in selected:
            print(source['name'] + ': SEEDED — not tested')
            continue
        count = sum(row['source'] == source['name'] for row in result['agents'])
        if source['health'] != 'ok' or source['mode'] != 'live':
            print(source['name'] + ': FAILED — ' + (source['error'] or 'Live access required.'))
            failed = True
        else:
            print(source['name'] + ': LIVE — ' + str(count) + ' visible agents')
    if failed:
        print('Resolve the reported access/configuration issue and retry. No seeded dashboard was started.')
        return 1
    if args.check_only:
        return 0
    print('Selected live reads succeeded. Starting local dashboard…', flush=True)
    if args.product:
        command = [sys.executable, str(Path(__file__).with_name('federation_app.py')), '--from-env', '--port', env.get('PORT','8770')]
    else:
        command = [sys.executable, str(Path(__file__).with_name('app.py'))]
    os.execve(sys.executable, command, env)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (KeyboardInterrupt, EOFError):
        print('\nConnection setup cancelled.')
        sys.exit(1)
