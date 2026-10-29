from network_policy import build_opener
"""Read-only federated inventory. Standard library only; no agent execution."""
import base64
import json
import os
import re
import threading
from datetime import datetime, timezone
from urllib.parse import urlencode, urlsplit, urljoin, parse_qs
from urllib.request import Request, HTTPRedirectHandler
from urllib.error import HTTPError


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


class ConnectorError(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class JSONResponse(dict):
    """JSON object with transport metadata needed for pagination."""
    def __init__(self, data, headers=None):
        super().__init__(data); self.headers=dict(headers or {})


def get_json(host, path, token, params):
    """Only HTTPS GET; redirects disabled to avoid forwarding bearer credentials."""
    parsed = urlsplit(host)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.path not in ('', '/') or parsed.query or parsed.fragment:
        raise ConnectorError('Configure an HTTPS origin without a path or embedded credentials.')
    if isinstance(token, tuple):
        username, password = token
        if ':' in username:
            raise ConnectorError('Basic authentication username cannot contain a colon.')
        authorization = 'Basic ' + base64.b64encode((username + ':' + password).encode('utf-8')).decode('ascii')
    else:
        authorization = 'Bearer ' + token
    request = Request(host.rstrip('/') + path + '?' + urlencode(params),
                      headers={'Authorization': authorization, 'Accept': 'application/json'})
    try:
        with build_opener(NoRedirect).open(request, timeout=10) as response:
            body = response.read(5_000_001)
            if len(body) > 5_000_000:
                raise ConnectorError('Response exceeds the demo size limit.')
            data = json.loads(body)
            if not isinstance(data, dict) or 'error' in data or data.get('errors'):
                raise ConnectorError('Unexpected API response shape.')
            return JSONResponse(data,getattr(response,'headers',{}))
    except HTTPError as error:
        raise ConnectorError('Read failed (HTTP %s). Check access, API availability and configuration.' % error.code) from None
    except ConnectorError:
        raise
    except Exception:
        # Never expose URLs, tokens, raw responses or network exception text.
        raise ConnectorError('Read failed. Check network, TLS and response format.') from None


def value(record, key, default=''):
    if not isinstance(record,dict):raise ConnectorError('Invalid source record.')
    item = record.get(key, default)
    if isinstance(item, dict):
        item = item.get('display_value') or item.get('value') or default
    if isinstance(item,(dict,list,tuple)):raise ConnectorError('Invalid source field shape; previous inventory retained.')
    return str(item) if item is not None else default


def normalize(source, kind, record, observed):
    if source == 'Databricks':
        identity = value(record, 'id') or value(record, 'name')
        name = value(record, 'display_name') or identity
        owner = value(record, 'creator') or 'Unassigned'
        raw = value(record, 'state') or 'UNKNOWN'
        status = {'ONLINE': 'active', 'READY': 'active', 'ACTIVE': 'active',
                  'CREATING': 'pending', 'UPDATING': 'pending', 'FAILED': 'error',
                  'ERROR': 'error'}.get(raw.upper(), 'unknown')
        updated = value(record, 'create_time')  # Creation is not an update timestamp.
    else:
        identity = value(record, 'sys_id')
        name = value(record, 'name') or identity
        owner = value(record, 'owned_by') or 'Unassigned'
        raw = value(record, 'active') or 'unknown'
        status = {'true': 'active', '1': 'active', 'false': 'inactive', '0': 'inactive'}.get(raw.lower(), 'unknown')
        updated = value(record, 'sys_updated_on')
    if not identity:
        raise ConnectorError('An API record has no stable identifier; refresh was not applied.')
    return dict(id=source.lower() + ':' + kind + ':' + identity, source_id=identity,
                name=name, owner=owner, status=status, source_status=raw, source=source,
                kind=kind, last_seen=observed, source_timestamp=updated,
                description=value(record, 'description'), provenance='live', stale=False,
                evidence={'owner_type':'Databricks creator; not a verified accountable owner','creator':value(record,'creator')} if source=='Databricks' else {})


def databricks(env, fetch=get_json):
    rows = []
    for kind, collection in [('Knowledge Assistant', 'knowledge-assistants'), ('Supervisor Agent', 'supervisor-agents')]:
        token, seen = '', set()
        for _ in range(100):
            params = {'page_size': 100}
            if token:
                params['page_token'] = token
            data = fetch(env['DATABRICKS_HOST'], '/api/2.1/' + collection, env['DATABRICKS_TOKEN'], params)
            if not isinstance(data,dict) or 'error' in data or data.get('errors'):raise ConnectorError('Databricks returned an error or incomplete response.')
            key = collection.replace('-', '_')
            if not isinstance(data.get(key), list):
                raise ConnectorError('Unexpected Databricks collection shape.')
            rows.extend(normalize('Databricks', kind, item, now()) for item in data[key])
            token = data.get('next_page_token')
            if token is not None and not isinstance(token,str):raise ConnectorError('Invalid Databricks pagination token; previous inventory retained.')
            if not token:
                break
            if token in seen:
                raise ConnectorError('Repeated pagination token; refresh was not applied.')
            seen.add(token)
        else:
            raise ConnectorError('Pagination exceeds demo limit; refresh was not applied.')
    return rows


def servicenow(env, fetch=get_json):
    table = env.get('SERVICENOW_TABLE', 'sn_aia_agent')
    owner = env.get('SERVICENOW_OWNER_FIELD', 'owned_by')
    if not re.fullmatch(r'[a-zA-Z0-9_]+', table) or not re.fullmatch(r'[a-zA-Z0-9_]+', owner):
        raise ConnectorError('Table and owner field must be simple identifiers.')
    credential = env.get('SERVICENOW_TOKEN') or (env.get('SERVICENOW_USERNAME', ''), env.get('SERVICENOW_PASSWORD', ''))
    rows = []
    for offset in range(0, 10000, 100):
        data = fetch(env['SERVICENOW_INSTANCE_URL'], '/api/now/table/' + table, credential, {
            'sysparm_limit': 100, 'sysparm_offset': offset,
            'sysparm_query': 'ORDERBYsys_id', 'sysparm_display_value': 'all',
            'sysparm_fields': ','.join(dict.fromkeys(['sys_id','name','description','active',owner,'sys_updated_on']))})
        if not isinstance(data,dict) or 'error' in data or data.get('errors'):raise ConnectorError('ServiceNow returned an error or incomplete response.')
        batch = data.get('result')
        if not isinstance(batch, list):
            raise ConnectorError('Unexpected ServiceNow collection shape.')
        for item in batch:
            mapped = dict(item)
            mapped['owned_by'] = item.get(owner)
            rows.append(normalize('ServiceNow', 'AI Agent', mapped, now()))
        headers={k.lower():v for k,v in getattr(data,'headers',{}).items()}
        link=headers.get('link','')
        next_links=re.findall(r'<([^>]+)>;\s*rel=["\']?next["\']?(?=\s*(?:,|$))',link)
        if not next_links:
            if re.search(r'rel=["\']?next',link):raise ConnectorError('Malformed ServiceNow pagination link.')
            return rows
        if len(next_links)!=1:raise ConnectorError('Ambiguous ServiceNow pagination link.')
        target=urlsplit(urljoin(env['SERVICENOW_INSTANCE_URL'],next_links[0]))
        host=urlsplit(env['SERVICENOW_INSTANCE_URL']);query=parse_qs(target.query)
        if ((target.scheme,target.netloc)!=(host.scheme,host.netloc) or target.path!='/api/now/table/'+table
                or target.fragment or query.get('sysparm_offset')!=[str(offset+100)]
                or query.get('sysparm_limit')!=['100']):
            raise ConnectorError('Invalid ServiceNow pagination link; inventory was not applied.')
    raise ConnectorError('Pagination exceeds demo limit; refresh was not applied.')


def seed(source):
    stamp = now()
    items = [('Revenue analyst', 'Demo · Finance', 'active'), ('Policy assistant', 'Demo · People Ops', 'active'),
             ('Operations supervisor', 'Unassigned', 'unknown')] if source == 'Databricks' else [
             ('Incident triage', 'Demo · IT Operations', 'active'), ('Access request helper', 'Demo · Service Desk', 'inactive'),
             ('Change risk reviewer', 'Unassigned', 'active')]
    return [dict(id=source.lower()+':seed:'+str(i), source_id='fixture-'+str(i), name=name, owner=owner,
                 status=status, source_status='SIMULATED', source=source,
                 kind=('Supervisor Agent' if i == 2 else 'Knowledge Assistant') if source == 'Databricks' else 'AI Agent',
                 last_seen=stamp, source_timestamp='', description='Fictional demo record; not retrieved from a vendor.',
                 provenance='seeded', stale=False) for i,(name,owner,status) in enumerate(items)]


class Registry:
    def __init__(self, env=None, fetch=get_json):
        self.env = dict(os.environ if env is None else env)
        self.fetch = fetch
        self.lock = threading.Lock()
        self.records = {}
        self.sources = {}

    def snapshot(self):
        with self.lock:
            return self._snapshot()

    def _snapshot(self):
        return {'agents': [r for rows in self.records.values() for r in rows],
                'sources': list(self.sources.values()), 'generated_at': now()}

    def refresh(self):
        with self.lock:
            for source, required, adapter in [
                ('Databricks', ['DATABRICKS_HOST','DATABRICKS_TOKEN'], databricks),
                ('ServiceNow', ['SERVICENOW_INSTANCE_URL','SERVICENOW_TOKEN'], servicenow)]:
                previous = self.sources.get(source, {})
                configured = [bool(self.env.get(k)) for k in required]
                if source == 'ServiceNow':
                    has_basic = bool(self.env.get('SERVICENOW_USERNAME') and self.env.get('SERVICENOW_PASSWORD'))
                    configured = [bool(self.env.get('SERVICENOW_INSTANCE_URL')), bool(self.env.get('SERVICENOW_TOKEN')) or has_basic]
                    if (self.env.get('SERVICENOW_USERNAME') or self.env.get('SERVICENOW_PASSWORD')) and not has_basic and not self.env.get('SERVICENOW_TOKEN'):
                        configured.append(False)
                        configured.append(True)
                mode = 'live' if all(configured) else 'seeded'
                info = dict(name=source, mode=mode, attempted_at=now(), last_success=previous.get('last_success'), error=None)
                try:
                    if any(configured) and not all(configured):
                        info['mode'] = 'configuration error'
                        raise ConnectorError('Incomplete credentials. Set host and complete credentials, or neither for seeded mode.')
                    rows = adapter(self.env, self.fetch) if mode == 'live' else seed(source)
                    if len({row['id'] for row in rows}) != len(rows):
                        raise ConnectorError('Duplicate source identifiers; refresh was not applied.')
                    self.records[source] = rows
                    info.update(health='ok', last_success=now())
                except Exception as error:
                    info.update(health='error', error=str(error) if isinstance(error, ConnectorError) else 'Unexpected adapter response; refresh was not applied.')
                    self.records[source] = [dict(row, stale=True) for row in self.records.get(source, [])]
                self.sources[source] = info
            return self._snapshot()
