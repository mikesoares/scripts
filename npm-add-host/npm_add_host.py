#!/usr/bin/env python3
"""Create or delete Nginx Proxy Manager proxy hosts (with Let's Encrypt certs) via the NPM API.

`add <fqdn> <port>` creates the host in NPM, forwarding to
NPM_FORWARD_HOST:<port>, with SSL, HTTP/2, HSTS, exploit blocking and
websocket support enabled. `delete <fqdn>` removes it again. The certificate is a per-host Let's
Encrypt cert issued with the HTTP-01 challenge, so it needs no DNS provider
integration -- only that the hostname already resolves to NPM (e.g. a wildcard
DNS record) and that NPM answers on port 80.

Standard library only. See README.md in this directory for configuration.
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Searched in order when --env-file is not given. The /etc path suits a shared
# host (mode 600, owned by the user that runs the script); the .env next to the
# script is the repo-wide convention.
DEFAULT_ENV_PATHS = ['/etc/npm-add-host/env', os.path.join(SCRIPT_DIR, '.env')]

# One DNS label: letters, digits and hyphens, not starting or ending with a hyphen.
LABEL_RE = re.compile(r'^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$')

# Issuing a cert makes NPM run certbot synchronously inside the request, which
# can take well over the default urllib timeout.
CERT_TIMEOUT = 180
API_TIMEOUT = 30

# Stored in a proxy host's `meta` to mark it as created by this script.
MANAGED_KEY = 'managed_by'
MANAGED_VALUE = 'npm-add-host'


# ============================================================
# Configuration
# ============================================================

def load_dotenv(path):
    """Load a .env file into os.environ. Skips blank lines and comments.

    Does not override variables that are already set in the environment,
    so real env vars (from the shell or cron) take precedence over the file.
    """
    if not os.path.isfile(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, _, value = line.partition('=')
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            if key and key not in os.environ:
                os.environ[key] = value


def env_bool(name, default):
    value = os.environ.get(name)
    if value is None or value == '':
        return default
    return value.strip().lower() in ('1', 'true', 'yes', 'on')


def parse_aliases(raw):
    """Parse 'name=id,name=id' into {name: id}. Ids must be numeric."""
    aliases = {}
    for entry in raw.split(','):
        entry = entry.strip()
        if not entry:
            continue
        name, sep, ident = entry.partition('=')
        name, ident = name.strip(), ident.strip()
        if not sep or not name or not ident.isdigit():
            sys.exit(f'Invalid NPM_ACCESS_LIST_ALIASES entry {entry!r}: expected name=<numeric id>')
        aliases[name] = ident
    return aliases


def load_config():
    """Read settings from the environment. Required ones are validated later."""
    return {
        'url': os.environ.get('NPM_URL', '').rstrip('/'),
        'email': os.environ.get('NPM_EMAIL', ''),
        'password': os.environ.get('NPM_PASSWORD', ''),
        'forward_host': os.environ.get('NPM_FORWARD_HOST', ''),
        'forward_scheme': os.environ.get('NPM_FORWARD_SCHEME', 'http'),
        'access_list': os.environ.get('NPM_ACCESS_LIST', ''),
        'access_list_aliases': parse_aliases(os.environ.get('NPM_ACCESS_LIST_ALIASES', '')),
        'le_email': os.environ.get('NPM_LE_EMAIL', '') or os.environ.get('NPM_EMAIL', ''),
        'websockets': env_bool('NPM_WEBSOCKETS', True),
        'hsts': env_bool('NPM_HSTS', True),
        'block_exploits': env_bool('NPM_BLOCK_EXPLOITS', True),
        'http2': env_bool('NPM_HTTP2', True),
        # Asset caching is off by default: it breaks dynamic apps (APIs, chat UIs).
        'caching': env_bool('NPM_CACHING', False),
    }


def validate_config(cfg, need_forward):
    required = {'NPM_URL': cfg['url'], 'NPM_EMAIL': cfg['email'], 'NPM_PASSWORD': cfg['password']}
    if need_forward:
        required['NPM_FORWARD_HOST'] = cfg['forward_host']
    missing = [k for k, v in required.items() if not v]
    if missing:
        sys.exit('Missing required settings: ' + ', '.join(missing)
                 + ' (set them in the env file or the environment)')
    if cfg['forward_scheme'] not in ('http', 'https'):
        sys.exit('NPM_FORWARD_SCHEME / --scheme must be http or https')


# ============================================================
# NPM API
# ============================================================

class NpmError(Exception):
    pass


class NpmClient:
    def __init__(self, base_url, verbose=False):
        self.base = base_url + '/api'
        self.token = None
        self.verbose = verbose

    def request(self, method, path, body=None, timeout=API_TIMEOUT):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header('Content-Type', 'application/json')
        if self.token:
            req.add_header('Authorization', 'Bearer ' + self.token)
        if self.verbose:
            print(f'  {method} {path}', file=sys.stderr)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors='replace')
            try:
                detail = json.loads(detail)['error']['message']
            except (ValueError, KeyError, TypeError):
                pass
            raise NpmError(f'{method} {path} -> HTTP {e.code}: {detail}') from None
        except (urllib.error.URLError, TimeoutError) as e:
            raise NpmError(f'{method} {path} failed: {e}') from None
        return json.loads(raw) if raw else None

    def login(self, email, password):
        result = self.request('POST', '/tokens', {'identity': email, 'secret': password})
        self.token = result['token']


# ============================================================
# Workflow
# ============================================================

def normalize_fqdn(name):
    """Validate a fully qualified hostname and return it lowercased."""
    fqdn = name.strip().lower().rstrip('.')
    labels = fqdn.split('.')
    if len(labels) < 2 or not all(LABEL_RE.match(label) for label in labels):
        sys.exit(f'Invalid hostname {name!r}: expected a fully qualified name '
                 'such as chat.example.com (letters, digits, hyphens)')
    return fqdn


def find_access_list(client, ref, aliases):
    """Resolve an access list given by alias, numeric id, or NPM name.

    Aliases come first because NPM does not enforce unique list names, so a name
    can be ambiguous; an alias pins a friendly name to one id.
    """
    ref = aliases.get(ref, ref)
    lists = client.request('GET', '/nginx/access-lists')
    if ref.isdigit():
        for al in lists:
            if al['id'] == int(ref):
                return al
        sys.exit(f'No access list with id {ref}')
    matches = [al for al in lists if al['name'] == ref]
    if not matches:
        names = ', '.join(sorted({al['name'] for al in lists})) or '(none)'
        sys.exit(f'No access list named {ref!r}. Available: {names}')
    if len(matches) > 1:
        ids = ', '.join(str(al['id']) for al in matches)
        sys.exit(f'Access list name {ref!r} is ambiguous (ids {ids}); pass the numeric id')
    return matches[0]


def find_existing_cert(client, fqdn):
    """Return a Let's Encrypt cert that covers exactly this host, if one exists."""
    for cert in client.request('GET', '/nginx/certificates'):
        if cert['provider'] == 'letsencrypt' and cert['domain_names'] == [fqdn]:
            return cert
    return None


def add_host(client, cfg, fqdn, port, access_list):
    for host in client.request('GET', '/nginx/proxy-hosts'):
        if fqdn in host['domain_names']:
            sys.exit(f'{fqdn} already exists in NPM as proxy host id {host["id"]}; '
                     'refusing to overwrite')

    cert = find_existing_cert(client, fqdn)
    cert_created = False
    if cert:
        print(f'Reusing existing certificate id {cert["id"]} for {fqdn}')
    else:
        print(f'Requesting Let\'s Encrypt certificate for {fqdn} (HTTP-01)...')
        cert = client.request('POST', '/nginx/certificates', {
            'provider': 'letsencrypt',
            'domain_names': [fqdn],
            'meta': {
                'letsencrypt_email': cfg['le_email'],
                'letsencrypt_agree': True,
                'dns_challenge': False,
            },
        }, timeout=CERT_TIMEOUT)
        cert_created = True

    payload = {
        'domain_names': [fqdn],
        'forward_scheme': cfg['forward_scheme'],
        'forward_host': cfg['forward_host'],
        'forward_port': port,
        'certificate_id': cert['id'],
        'ssl_forced': True,
        'http2_support': cfg['http2'],
        'hsts_enabled': cfg['hsts'],
        'hsts_subdomains': False,
        'block_exploits': cfg['block_exploits'],
        'caching_enabled': cfg['caching'],
        'allow_websocket_upgrade': cfg['websockets'],
        'access_list_id': access_list['id'] if access_list else 0,
        'advanced_config': '',
        'enabled': True,
        # Marker so `delete` only removes hosts this script created, never the
        # hand-made ones that share the same NPM.
        'meta': {'letsencrypt_agree': False, 'dns_challenge': False, MANAGED_KEY: MANAGED_VALUE},
        'locations': [],
    }
    try:
        host = client.request('POST', '/nginx/proxy-hosts', payload)
    except NpmError:
        # Don't leave an orphaned cert behind (it would count against Let's
        # Encrypt rate limits on a retry) -- but never delete one we reused.
        if cert_created:
            try:
                client.request('DELETE', f'/nginx/certificates/{cert["id"]}')
                print(f'Rolled back certificate id {cert["id"]}', file=sys.stderr)
            except NpmError as e:
                print(f'Could not roll back certificate id {cert["id"]}: {e}', file=sys.stderr)
        raise
    return host


def find_deletable_host(client, fqdn, force):
    """Return (host, cert_id, cert_shared) for fqdn, or exit if it must not be deleted."""
    hosts = client.request('GET', '/nginx/proxy-hosts')
    host = next((h for h in hosts if fqdn in h['domain_names']), None)
    if not host:
        sys.exit(f'No proxy host for {fqdn}')
    if host['domain_names'] != [fqdn]:
        # Deleting would also take down the other names, which is never what
        # a one-hostname command should do silently.
        sys.exit(f'Proxy host id {host["id"]} serves several names '
                 f'({", ".join(host["domain_names"])}); delete it in the NPM UI')
    if (host.get('meta') or {}).get(MANAGED_KEY) != MANAGED_VALUE and not force:
        sys.exit(f'{fqdn} (proxy host id {host["id"]}) was not created by this script; '
                 'pass --force to delete it anyway')

    cert_id = host.get('certificate_id') or 0
    cert_shared = any(h['id'] != host['id'] and h.get('certificate_id') == cert_id for h in hosts)
    return host, cert_id, cert_shared


# ============================================================
# CLI
# ============================================================

def build_parser():
    p = argparse.ArgumentParser(
        description='Create or delete Nginx Proxy Manager proxy hosts via the NPM API.',
        epilog='Settings come from environment variables; see README.md.')
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--env-file', help='path to env file (default: /etc/npm-add-host/env, then .env next to the script)')
    common.add_argument('--dry-run', action='store_true', help='validate and print the plan without changing NPM')
    common.add_argument('-v', '--verbose', action='store_true', help='log API calls to stderr')

    sub = p.add_subparsers(dest='command', required=True)

    add = sub.add_parser('add', parents=[common], help='create a proxy host and Let\'s Encrypt cert')
    add.add_argument('fqdn', help='fully qualified hostname, e.g. chat.example.com')
    add.add_argument('port', type=int, help='upstream port')
    add.add_argument('--forward-host', help='upstream host/IP (default: NPM_FORWARD_HOST)')
    add.add_argument('--scheme', choices=('http', 'https'), help='upstream scheme (default: NPM_FORWARD_SCHEME or http)')
    add.add_argument('--access-list', help='access list alias, NPM name, or numeric id (default: NPM_ACCESS_LIST)')
    add.add_argument('--public', action='store_true',
                     help='allow creating a host with no access list (required if none is configured)')
    add.add_argument('--no-websockets', action='store_true', help='disable websocket upgrade support')
    add.add_argument('--no-hsts', action='store_true', help='disable HSTS')

    rm = sub.add_parser('delete', parents=[common], help='delete a proxy host (and its cert if unused)')
    rm.add_argument('fqdn', help='fully qualified hostname of the host to delete')
    rm.add_argument('--keep-cert', action='store_true', help='leave the Let\'s Encrypt certificate in NPM')
    rm.add_argument('--force', action='store_true', help='also delete hosts not created by this script')
    rm.add_argument('-y', '--yes', action='store_true', help='skip the confirmation prompt')

    cfg = sub.add_parser('show-config', parents=[common], help='print effective configuration (password masked)')
    cfg.set_defaults(fqdn=None)
    return p


def load_env(env_file):
    if env_file:
        if not os.path.isfile(env_file):
            sys.exit(f'Env file not found: {env_file}')
        load_dotenv(env_file)
    else:
        for path in DEFAULT_ENV_PATHS:
            load_dotenv(path)


def run_add(client, cfg, args):
    if args.forward_host:
        cfg['forward_host'] = args.forward_host
    if args.scheme:
        cfg['forward_scheme'] = args.scheme
    if args.access_list:
        cfg['access_list'] = args.access_list
    if args.no_websockets:
        cfg['websockets'] = False
    if args.no_hsts:
        cfg['hsts'] = False
    validate_config(cfg, need_forward=True)
    if not 1 <= args.port <= 65535:
        sys.exit('PORT must be 1-65535')
    if not cfg['access_list'] and not args.public:
        # Fail closed: a forgotten access list should not silently publish a service.
        sys.exit('No access list configured. Set NPM_ACCESS_LIST (or --access-list), '
                 'or pass --public to deliberately create an unrestricted host.')

    fqdn = normalize_fqdn(args.fqdn)
    client.login(cfg['email'], cfg['password'])
    access_list = find_access_list(client, cfg['access_list'], cfg['access_list_aliases']) if cfg['access_list'] else None
    acl_text = f'{access_list["name"]} (id {access_list["id"]})' if access_list else 'none (public)'
    plan = (f'{fqdn} -> {cfg["forward_scheme"]}://{cfg["forward_host"]}:{args.port}  '
            f'[access list: {acl_text}; websockets={cfg["websockets"]}, hsts={cfg["hsts"]}]')
    if args.dry_run:
        print('Dry run, would create: ' + plan)
        return
    print('Creating: ' + plan)
    host = add_host(client, cfg, fqdn, args.port, access_list)
    print(f'Created proxy host id {host["id"]}: https://{fqdn}')


def run_delete(client, cfg, args):
    validate_config(cfg, need_forward=False)
    fqdn = normalize_fqdn(args.fqdn)
    client.login(cfg['email'], cfg['password'])
    host, cert_id, cert_shared = find_deletable_host(client, fqdn, args.force)

    drop_cert = bool(cert_id) and not args.keep_cert and not cert_shared
    if drop_cert:
        cert = client.request('GET', f'/nginx/certificates/{cert_id}')
        # Only drop a cert that exists solely for this hostname; a multi-name or
        # custom cert may be wanted elsewhere.
        drop_cert = cert['provider'] == 'letsencrypt' and cert['domain_names'] == [fqdn]
    plan = (f'proxy host id {host["id"]} ({fqdn})'
            + (f' and certificate id {cert_id}' if drop_cert else ' (certificate kept)'))
    if args.dry_run:
        print('Dry run, would delete ' + plan)
        return
    if not args.yes:
        if not sys.stdin.isatty():
            sys.exit('Refusing to delete without a terminal; pass --yes to confirm')
        if input(f'Delete {plan}? [y/N] ').strip().lower() != 'y':
            sys.exit('Aborted')
    client.request('DELETE', f'/nginx/proxy-hosts/{host["id"]}')
    print(f'Deleted proxy host id {host["id"]}')
    if drop_cert:
        client.request('DELETE', f'/nginx/certificates/{cert_id}')
        print(f'Deleted certificate id {cert_id}')


def main():
    args = build_parser().parse_args()
    load_env(args.env_file)
    cfg = load_config()

    if args.command == 'show-config':
        shown = dict(cfg, password='***' if cfg['password'] else '')
        for key, value in shown.items():
            print(f'{key}: {value}')
        return

    client = NpmClient(cfg['url'], args.verbose)
    try:
        if args.command == 'add':
            run_add(client, cfg, args)
        else:
            run_delete(client, cfg, args)
    except NpmError as e:
        sys.exit(f'NPM error: {e}')


if __name__ == '__main__':
    main()
