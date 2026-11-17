# kuma-maintenance

Put [Uptime Kuma](https://github.com/louislam/uptime-kuma) monitors into maintenance, and back out, from the command line. Monitors are selected by tag (default: the `host` tag), across one or several Kuma instances, so a planned reboot or update of a machine silences exactly the checks that watch it, wherever they run.

**Requirements:** Python 3.6+, plus `python-socketio` and `websocket-client` (Debian: `apt install python3-socketio python3-websocket`; otherwise `pip install "python-socketio[client]"`). Uptime Kuma v2. A web-UI login that exists on every instance: Kuma API keys only authorise `/metrics` and cannot schedule maintenance.

## Usage

```bash
python kuma_maintenance.py start --tag myserver              # maintenance on every monitor tagged host=myserver
python kuma_maintenance.py start --tag myserver --dry-run    # show which monitors, change nothing
python kuma_maintenance.py start --tag myserver --instance main   # only one configured instance
python kuma_maintenance.py end --tag myserver                # remove the window again
python kuma_maintenance.py status                            # windows this script created
```

| Flag | Applies to | Description |
|------|------------|-------------|
| `--tag VALUE` | `start`, `end` | Value of the tag (`KUMA_TAG_NAME`, default `host`) that selects monitors |
| `--instance NAME` | all | Limit to one instance from `KUMA_INSTANCES` (repeatable) |
| `--dry-run` | all | Log in and report, change nothing |
| `--env-file PATH` | global, before the command | Env file (default: `/etc/kuma-maintenance/env`, then `.env` next to the script) |

Typical use around a reboot:

```bash
python kuma_maintenance.py start --tag myserver && ssh myserver sudo reboot
# ... wait until it is back and healthy ...
python kuma_maintenance.py end --tag myserver
```

## Behaviour

- `start` creates a **manual** maintenance window per instance (active until removed) titled `kuma-maintenance: <tag value>` and attaches the matching monitors. An instance with no matching monitors is skipped. If no instance matches anything, it exits 1, so a typo cannot lead to a reboot with nothing silenced.
- Running `start` again while the window exists leaves it alone.
- `end` and `status` only look at windows with that title prefix; hand-made maintenance windows are never touched.
- Exit status is 1 if any instance could not be reached or rejected the request; the other instances are still processed.

## Configuration

Copy `.env.example` to `.env` next to the script (or to `/etc/kuma-maintenance/env`, mode 600). Variables already set in the environment take precedence.

| Variable | Required | Description |
|----------|----------|-------------|
| `KUMA_INSTANCES` | yes | Comma-separated `name=url` entries, e.g. `main=http://kuma-a.example:3001,remote=http://kuma-b.example:3001` |
| `KUMA_USER`, `KUMA_PASS` | yes | Web-UI login valid on every instance |
| `KUMA_TAG_NAME` | no | Tag used to select monitors (default `host`) |

An instance only reachable through a tunnel (for example `ssh -N -L 13001:kuma-host:3001 jump`) can be listed with its local URL (`http://127.0.0.1:13001`) while the tunnel is up.
