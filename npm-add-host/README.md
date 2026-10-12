# npm-add-host

Create or delete [Nginx Proxy Manager](https://nginxproxymanager.com/) proxy hosts from the command line through NPM's REST API. One command makes a hostname forward to an upstream `host:port`, with a per-host Let's Encrypt certificate and sensible defaults.

**Requirements:** Python 3.6+, standard library only. NPM 2.x with its admin API reachable from where you run the script.

**Prerequisite:** the new hostname must already resolve to NPM (typically a wildcard DNS record such as `*.example.com`) and NPM must answer on port 80. Certificates use the HTTP-01 challenge, so no DNS-provider integration is needed.

## Usage

```bash
python npm_add_host.py add chat.example.com 3000        # -> <NPM_FORWARD_HOST>:3000
python npm_add_host.py add api.example.com 4000 --forward-host 192.0.2.20 --scheme https
python npm_add_host.py add wiki.example.com 8080 --access-list internal --no-websockets
python npm_add_host.py add demo.example.com 9000 --public   # deliberately no access list
python npm_add_host.py add chat.example.com 3000 --dry-run  # validate and show the plan only
python npm_add_host.py delete chat.example.com              # asks for confirmation
python npm_add_host.py delete chat.example.com --yes        # no prompt (required without a terminal)
python npm_add_host.py show-config                          # effective settings (password masked)
```

Both commands take the fully qualified hostname.

**`add <fqdn> <port>`**

| Flag | Description |
|------|-------------|
| `--forward-host HOST` | Upstream host or IP (default `NPM_FORWARD_HOST`) |
| `--scheme {http,https}` | Upstream scheme (default `NPM_FORWARD_SCHEME`, else `http`) |
| `--access-list REF` | Access list to attach: an alias from `NPM_ACCESS_LIST_ALIASES`, an NPM list name, or a numeric id (default `NPM_ACCESS_LIST`) |
| `--public` | Allow creating a host with no access list |
| `--no-websockets` | Disable websocket upgrade support |
| `--no-hsts` | Disable HSTS |

**`delete <fqdn>`**

| Flag | Description |
|------|-------------|
| `--keep-cert` | Leave the Let's Encrypt certificate in NPM |
| `--force` | Also delete hosts this script did not create |
| `-y`, `--yes` | Skip the confirmation prompt |

**Flags on every command:** `--env-file PATH` (default: `/etc/npm-add-host/env`, then `.env` next to the script), `--dry-run` (log in and validate, change nothing), `-v`/`--verbose` (log API calls to stderr).

## What it creates

- A Let's Encrypt certificate for exactly that hostname (an existing one is reused).
- A proxy host with SSL forced, HTTP/2, HSTS, Block Common Exploits and websocket support on, asset caching off (each is configurable below).
- It refuses to touch a hostname that already has a proxy host, and deletes the certificate it just issued if host creation then fails.
- Each host it creates is tagged in NPM (`meta.managed_by`), which is what lets `delete` tell its own hosts from hand-made ones.

## Deleting

`delete` removes the proxy host and, when nothing else uses it, its certificate. Guards, because the same NPM usually holds many hand-made hosts:

- Only hosts tagged by this script are deleted; anything else needs `--force`.
- A host that serves several names is refused (delete it in the NPM UI).
- The certificate is deleted only if it is a Let's Encrypt cert for exactly that hostname and no other host uses it.
- Without a terminal, `--yes` is required.

**Fail-closed access:** with no `NPM_ACCESS_LIST` / `--access-list`, the script refuses to run unless `--public` is passed, so a missing setting never publishes a service by accident.

## Configuration

All settings are environment variables, loaded from an env file (see `.env.example`). Variables already set in the shell take precedence over the file. The file holds a login: `chmod 600` it and keep it out of git.

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `NPM_URL` | Yes | — | Base URL of the NPM admin API (the admin port) |
| `NPM_EMAIL` | Yes | — | Login of a dedicated NPM user for this script |
| `NPM_PASSWORD` | Yes | — | That user's password |
| `NPM_FORWARD_HOST` | For `add` | — | Default upstream host or IP |
| `NPM_ACCESS_LIST_ALIASES` | No | — | Comma-separated `name=<numeric id>` pairs, e.g. `internal=1,external=2`. NPM allows duplicate list names, so aliases give `--access-list` an unambiguous handle |
| `NPM_ACCESS_LIST` | No | — | Default access list (alias, name or id); without it (or `--access-list`) `--public` is required |
| `NPM_FORWARD_SCHEME` | No | `http` | Default upstream scheme |
| `NPM_LE_EMAIL` | No | `NPM_EMAIL` | Let's Encrypt registration email |
| `NPM_WEBSOCKETS` | No | `true` | Websocket upgrade support |
| `NPM_HSTS` | No | `true` | HSTS (without subdomains) |
| `NPM_BLOCK_EXPLOITS` | No | `true` | Block Common Exploits |
| `NPM_HTTP2` | No | `true` | HTTP/2 |
| `NPM_CACHING` | No | `false` | Asset caching (off: it breaks dynamic apps) |

## Notes

- `add` is not idempotent by design: an existing hostname is an error, not an update.
- Issuing a certificate runs certbot inside NPM and can take up to a minute. Let's Encrypt limits new certificates per registered domain per week, so avoid scripting bulk create/delete loops.
- The NPM API is not formally versioned. Re-test after upgrading NPM.
