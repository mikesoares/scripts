#!/usr/bin/env python3
"""Put Uptime Kuma monitors into (and out of) maintenance from the command line.

`start --tag VALUE` creates a manual maintenance window on every configured
Kuma instance, covering the monitors whose `host` tag equals VALUE (the tag
name is configurable). `end --tag VALUE` removes those windows again. Use it
around planned reboots and updates so they do not page anyone.

Talks to Kuma's Socket.IO API (the same one the web UI uses), logging in with a
username and password: API keys only authorise /metrics and cannot schedule
maintenance. Requires the python-socketio and websocket-client packages
(Debian: `apt install python3-socketio python3-websocket`).

See README.md in this directory for configuration.
"""

import argparse
import os
import sys
import threading

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Searched in order when --env-file is not given. The /etc path suits a shared
# host (mode 600, owned by the user that runs the script); the .env next to the
# script is the repo-wide convention.
DEFAULT_ENV_PATHS = ['/etc/kuma-maintenance/env', os.path.join(SCRIPT_DIR, '.env')]

# Windows created by this script carry this title prefix. `end` only deletes
# windows with the prefix, so hand-made maintenance windows are never touched.
TITLE_PREFIX = 'kuma-maintenance: '

CALL_TIMEOUT = 30   # seconds for each Socket.IO request
LIST_TIMEOUT = 20   # seconds to wait for Kuma to push its monitor/maintenance lists


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


def parse_instances(raw):
    """Parse 'name=url,name=url' into an ordered {name: url} dict."""
    instances = {}
    for entry in raw.split(','):
        entry = entry.strip()
        if not entry:
            continue
        name, sep, url = entry.partition('=')
        name, url = name.strip(), url.strip()
        if not sep or not name or not url:
            raise ValueError("bad KUMA_INSTANCES entry %r (expected name=url)" % entry)
        instances[name] = url
    return instances


def load_config():
    return {
        'instances': os.environ.get('KUMA_INSTANCES', ''),
        'user': os.environ.get('KUMA_USER', ''),
        'password': os.environ.get('KUMA_PASS', ''),
        'tag_name': os.environ.get('KUMA_TAG_NAME', '').strip() or 'host',
    }


# ============================================================
# Kuma Socket.IO API
# ============================================================

class KumaError(Exception):
    pass


class KumaClient:
    """One logged-in Socket.IO session to a Kuma instance."""

    def __init__(self, url, user, password):
        import socketio  # imported late so --help works without the dependency
        self._sio = socketio.Client()
        self._got = {'monitorList': threading.Event(), 'maintenanceList': threading.Event()}
        self.monitors = {}
        self.maintenances = {}

        # Kuma pushes both lists right after a successful login.
        @self._sio.on('monitorList')
        def _monitors(data):
            self.monitors = data
            self._got['monitorList'].set()

        @self._sio.on('maintenanceList')
        def _maintenances(data):
            self.maintenances = data
            self._got['maintenanceList'].set()

        try:
            self._sio.connect(url, wait_timeout=CALL_TIMEOUT)
        except Exception as e:
            raise KumaError("cannot connect to %s: %s" % (url, e))
        reply = self._call('login', {'username': user, 'password': password, 'token': ''})
        if not reply.get('ok'):
            self.close()
            raise KumaError("login rejected: %s" % reply.get('msg', 'unknown error'))
        for name, event in self._got.items():
            if not event.wait(LIST_TIMEOUT):
                self.close()
                raise KumaError("timed out waiting for Kuma's %s" % name)

    def _call(self, event, *args):
        try:
            reply = self._sio.call(event, args if len(args) > 1 else args[0],
                                   timeout=CALL_TIMEOUT)
        except Exception as e:
            raise KumaError("%s failed: %s" % (event, e))
        if isinstance(reply, dict) and reply.get('ok') is False:
            raise KumaError("%s: %s" % (event, reply.get('msg', 'rejected')))
        return reply

    def monitors_with_tag(self, tag_name, tag_value):
        """Return [(id, name)] for monitors carrying tag_name=tag_value."""
        found = []
        for mid, mon in self.monitors.items():
            for tag in mon.get('tags', []):
                if tag.get('name') == tag_name and tag.get('value') == tag_value:
                    found.append((int(mid), mon.get('name', str(mid))))
                    break
        return sorted(found)

    def windows_titled(self, title):
        """Return the ids of maintenance windows with exactly this title."""
        return [int(i) for i, m in self.maintenances.items() if m.get('title') == title]

    def start_window(self, title, description, monitor_ids):
        # strategy "manual" means active until deleted; the date/time fields are
        # required by the API but ignored for this strategy.
        reply = self._call('addMaintenance', {
            'title': title,
            'description': description,
            'strategy': 'manual',
            'active': True,
            'intervalDay': 1,
            'dateRange': [None],
            'timeRange': [{'hours': 2, 'minutes': 0}, {'hours': 3, 'minutes': 0}],
            'weekdays': [],
            'daysOfMonth': [],
            'timezoneOption': 'UTC',
        })
        window_id = reply['maintenanceID']
        # Attach the monitors; if that fails, remove the empty window again so a
        # retry does not leave a stray one behind.
        try:
            self._call('addMonitorMaintenance', window_id, [{'id': i} for i in monitor_ids])
        except KumaError:
            self._call('deleteMaintenance', window_id)
            raise
        return window_id

    def delete_window(self, window_id):
        self._call('deleteMaintenance', window_id)

    def close(self):
        try:
            self._sio.disconnect()
        except Exception:
            pass


# ============================================================
# Workflow
# ============================================================

def window_title(tag_value):
    return TITLE_PREFIX + tag_value


def run_start(client, name, cfg, args):
    matches = client.monitors_with_tag(cfg['tag_name'], args.tag)
    if not matches:
        print("%s: no monitors tagged %s=%s, skipping" % (name, cfg['tag_name'], args.tag))
        return 0
    title = window_title(args.tag)
    existing = client.windows_titled(title)
    if existing:
        print("%s: already in maintenance via '%s' (window id %s), leaving it" % (name, title, existing[0]))
        return 1
    print("%s: %d monitor(s) -> maintenance: %s" % (name, len(matches), ', '.join(n for _, n in matches)))
    if args.dry_run:
        return len(matches)
    client.start_window(title, "Planned work on %s=%s" % (cfg['tag_name'], args.tag),
                        [i for i, _ in matches])
    return len(matches)


def run_end(client, name, cfg, args):
    ids = client.windows_titled(window_title(args.tag))
    if not ids:
        print("%s: no '%s' window found, skipping" % (name, window_title(args.tag)))
        return 0
    for window_id in ids:
        print("%s: removing maintenance window %d" % (name, window_id))
        if not args.dry_run:
            client.delete_window(window_id)
    return len(ids)


def run_status(client, name, cfg, args):
    ours = {i: m for i, m in client.maintenances.items()
            if str(m.get('title', '')).startswith(TITLE_PREFIX)}
    if not ours:
        print("%s: no windows created by this script" % name)
    for i, m in sorted(ours.items(), key=lambda x: int(x[0])):
        print("%s: id %s  %s  [%s]" % (name, i, m['title'], m.get('status', '?')))
    return len(ours)


COMMANDS = {'start': run_start, 'end': run_end, 'status': run_status}


# ============================================================
# CLI
# ============================================================

def build_parser():
    p = argparse.ArgumentParser(
        description="Put Uptime Kuma monitors into or out of maintenance, selected by tag.",
        epilog="Configuration comes from environment variables or a .env file; see README.md.")
    p.add_argument('--env-file', help="env file (default: /etc/kuma-maintenance/env, then .env next to the script)")
    sub = p.add_subparsers(dest='command', required=True)
    for name, text in (('start', "start a maintenance window for the monitors with this tag value"),
                       ('end', "remove the window 'start' created"),
                       ('status', "list windows this script created")):
        sp = sub.add_parser(name, help=text)
        sp.add_argument('--instance', action='append', metavar='NAME',
                        help="only this Kuma instance (repeatable; default: all in KUMA_INSTANCES)")
        sp.add_argument('--dry-run', action='store_true', help="show what would change, change nothing")
        if name != 'status':
            sp.add_argument('--tag', required=True, metavar='VALUE',
                            help="value of the tag (KUMA_TAG_NAME, default 'host') that selects monitors")
    return p


def main():
    args = build_parser().parse_args()
    if args.env_file:
        if not os.path.isfile(args.env_file):
            sys.exit("env file not found: %s" % args.env_file)
        load_dotenv(args.env_file)
    else:
        for path in DEFAULT_ENV_PATHS:
            load_dotenv(path)
    cfg = load_config()

    try:
        instances = parse_instances(cfg['instances'])
    except ValueError as e:
        sys.exit(str(e))
    missing = [k for k, v in (('KUMA_INSTANCES', instances), ('KUMA_USER', cfg['user']),
                              ('KUMA_PASS', cfg['password'])) if not v]
    if missing:
        sys.exit("missing configuration: %s (see README.md)" % ', '.join(missing))
    if args.instance:
        unknown = [n for n in args.instance if n not in instances]
        if unknown:
            sys.exit("unknown instance(s): %s (configured: %s)" % (', '.join(unknown), ', '.join(instances)))
        instances = {n: instances[n] for n in args.instance}

    try:
        import socketio  # noqa: F401
    except ImportError:
        sys.exit("python-socketio is required (Debian: apt install python3-socketio python3-websocket)")

    handler = COMMANDS[args.command]
    failed = False
    touched = 0
    for name, url in instances.items():
        try:
            client = KumaClient(url, cfg['user'], cfg['password'])
        except KumaError as e:
            print("%s: %s" % (name, e), file=sys.stderr)
            failed = True
            continue
        try:
            touched += handler(client, name, cfg, args)
        except KumaError as e:
            print("%s: %s" % (name, e), file=sys.stderr)
            failed = True
        finally:
            client.close()

    # A tag that matches nothing anywhere is almost certainly a typo; fail so a
    # calling script does not go on to reboot with nothing in maintenance.
    if args.command == 'start' and touched == 0 and not failed:
        print("no monitors matched on any instance", file=sys.stderr)
        failed = True
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
