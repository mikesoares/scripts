# kuma-maintenance Architecture

## Code Structure

`kuma-maintenance/kuma_maintenance.py` imports only the standard library at module level; `socketio` is imported lazily so `--help` works without it. Top to bottom: Configuration (`load_dotenv()`, `parse_instances()`, `load_config()`), Kuma Socket.IO API (`KumaClient`, `KumaError`), Workflow (`run_start`, `run_end`, `run_status` in the `COMMANDS` dispatch table), CLI (`build_parser()`, `main()`).

## Design Decisions

- **Socket.IO with a username/password, not the REST API or API keys.** Kuma has no REST endpoint for maintenance, and API keys only authorise `/metrics`. The web UI's own Socket.IO events (`login`, `addMaintenance`, `addMonitorMaintenance`, `deleteMaintenance`) are the only path.
- **Manual strategy.** The window stays active until `end` removes it, so a reboot that takes longer than planned never un-silences the monitors. The date and time fields in the payload are required by the API but ignored for this strategy.
- **Title prefix as ownership marker** (`kuma-maintenance: `). `end`/`status` match on it, so hand-made windows are never deleted. Same idea as `npm-add-host`'s `meta.managed_by`.
- **Select by tag, not monitor id.** Ids differ per Kuma instance; a tag value (the machine name) works on all of them, and new monitors are covered as soon as they are tagged.
- **Fail closed on `start`.** Zero matching monitors on every instance exits 1 (a typo must not let a reboot proceed unsilenced). `addMonitorMaintenance` failing deletes the just-created empty window.
- **Env search order:** `--env-file`, else `/etc/kuma-maintenance/env` then `.env` next to the script; real environment variables win.

## Gotchas

- After `login`, Kuma pushes `monitorList` and `maintenanceList` as events; `KumaClient` waits for both before the workflow reads them.
- Tested live on Uptime Kuma v2 with Debian's python3-socketio. The Socket.IO API is not a documented, versioned interface; re-test after a Kuma upgrade.
- No automated tests; verification is `start --dry-run`, a real `start`, `status`, `end` against a tagged test monitor.
