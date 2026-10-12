# TODOs

## Backlog

- [ ] **[Debt]** `npm-add-host/npm_add_host.py` has no automated tests; add unit tests for `parse_aliases`, `normalize_fqdn`, `find_access_list`, and the delete guards against a stubbed `NpmClient`
- [ ] **[Debt]** `npm-add-host`: support an `https://` `NPM_URL` with a custom CA bundle (it currently talks to the admin API over plain `http://` on a trusted LAN)
- [ ] **[Debt]** `npm-add-host`: if NPM ever unpins `certbot-dns-ispconfig` (plugin 0.3.1 fixes the empty `stamp` bug), add an optional DNS-01 wildcard mode
- [ ] **[Docs]** `optimize_images.py` is undocumented in both README.md and CLAUDE.md (drift violation — should be documented or relocated to `qsda/`)
- [ ] Phase 3: write `photo-flatten.py` — flatten deeply nested directories (depth >= 3) to 1–2 levels based on DB analysis
- [x] Phase 2: write `photo-rename-dirs.py` — read `photo-db-scan.py` DB output and propose/execute directory renames (add full YYYY-MM-DD prefix, normalize partial/missing dirs)

## Features — Done

- [x] Add Telegram notifications as an optional alert channel (interface-bound via `curl --interface`)
- [x] Add WHOIS/ISP verification to detect routing through wrong ISP
- [x] Add CLI flags for per-invocation feature overrides (`--email`/`--no-email`, `--telegram`/`--no-telegram`, `--whois`/`--no-whois`)
- [x] Add `--dry-run` mode for testing without side effects
- [x] Add `--show-config` to print effective configuration
- [x] Add `--test-alerts` to send test messages through email/Telegram
- [x] Add `--test-whois` to test WHOIS/ISP lookup and show org name
- [x] Add `--help` with feature descriptions, config instructions, and usage examples
- [x] Add built-in `.env` file loading (`load_dotenv()`) with `--env-file` flag
- [x] Restructure code into clear sections (Configuration, State, Connectivity, SMTP, Notifications, CLI, Main)

## Technical Debt — Done

- [x] **[Debt]** Extract hardcoded config (interfaces, SMTP, websites) from `check_connectivity.py` into environment variables or a config file
- [x] **[Debt]** Add `.env.example` once config is externalized
- [x] **[Debt]** Support comma-separated fallback list for `IP_LOOKUP_URL` (try each in order if one is down)
