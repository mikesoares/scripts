---
globs: ["check_connectivity*"]
---

# check_connectivity.py Architecture

## Code Structure

The script is organized into clear sections, top-to-bottom:

| Section | Contents |
|---------|----------|
| Configuration | `Interface` namedtuple, `load_dotenv()`, `parse_interfaces()`, `load_config()`, `resolve_feature_flags()` |
| State Persistence | `load_state()`, `save_state()` |
| Connectivity Checks | `check_connectivity()`, `_curl_request()`, `_run_whois()`, `_lookup_public_ip()`, `verify_isp()` |
| Interface-Bound SMTP | `_BoundSMTP`, `_BoundSMTP_SSL` |
| Notifications | `_format_alert_body()`, `_send_email()`, `_send_telegram()`, `notify()` |
| CLI | `build_parser()`, `show_config()`, `test_alerts()`, `test_whois()` |
| Main | `main()` |

## Connectivity Check Details

DNS resolution uses the system default route, not the bound interface — intentional, since we only care whether the interface can carry TCP traffic. `SO_BINDTODEVICE` binds the TCP socket to the specified interface for the HTTPS connection.

## Notification Channels

Both channels are optional and independently configurable. The `notify()` dispatcher calls each enabled channel, routing through the working interface when available.

**Email (interface-bound SMTP):** Uses `_BoundSMTP` / `_BoundSMTP_SSL` — smtplib subclasses that override `_get_socket()` to bind the SMTP socket via `SO_BINDTODEVICE`. Falls back to unbound smtplib (OS-chosen route) when no interface is working. Requires all 5 SMTP env vars to be present.

**Telegram (interface-bound curl):** Uses `_curl_request()` with `curl --interface` to POST to the Telegram Bot API. This avoids adding `requests` as a dependency — the script stays stdlib-only (plus `curl` and `whois` CLI tools). Requires `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.

## WHOIS / ISP Verification

When enabled (`WHOIS_ENABLED=true`), after an interface passes the basic connectivity check, the script verifies it's routing through the expected ISP:

1. `_lookup_public_ip()` tries each URL in `IP_LOOKUP_URL` in order until one returns a public IP (fallback chain for resilience)
2. `_run_whois()` runs `whois <ip>` and parses the org name (handles ARIN `OrgName:`, RIPE `org-name:`, and generic `Organization:` formats)
3. `verify_isp()` compares using case-insensitive substring matching (handles "Bell Canada" vs "BELL CANADA" vs "Bell Canada Inc.")

**Graceful fallback:** If all IP lookup URLs fail or WHOIS fails, the interface is treated as "up" — only an explicit org mismatch marks it down. This prevents false alerts when lookup services are unavailable.

The expected ISP org is configured per-interface via the third field in the `INTERFACES` env var (e.g., `eth0:Primary:BELL CANADA`). Interfaces without an expected org skip WHOIS verification even when globally enabled.

## CSV Resilience

`load_state()` validates that each row has exactly 2 columns with a valid status value (`up`/`down`). If the CSV is malformed, it returns empty state — effectively treating it as a first run, which reinitializes the file with current values on the next `save_state()` call without triggering false alerts.

## Configuration Design Principles

- **`.env` auto-loading:** `load_dotenv()` reads key=value pairs, skips comments and blanks, strips optional quotes, and does not override existing env vars. This eliminates the need for `set -a && . .env && set +a` in cron.
- **Each alert channel is optional:** Missing env vars disable the channel; the script still checks connectivity and saves state.
- **CLI flags override env defaults:** `--email` force-enables (errors if vars missing), `--no-email` force-disables, no flag uses env default. Implemented in `resolve_feature_flags()`.
- **Interfaces format:** `INTERFACES` env var uses comma-separated `name:label[:expected_org]` entries parsed by `parse_interfaces()`. A bare name (no colon) uses the name as its own label.
- **Defaults match the original hardcoded values** so existing deployments keep working without setting `INTERFACES` or `WEBSITES`.

## Runtime Artifacts

| File | Description |
|------|-------------|
| `interface_states.csv` | Persists interface up/down state between runs. Two columns: interface name, status (`up`/`down`). Created automatically on first run. |

## Key Constraints

- **Linux-only:** `SO_BINDTODEVICE` is not available on macOS/Windows
- **Root required:** Binding to a specific interface requires elevated privileges
- **No external Python dependencies:** Uses only Python stdlib
- **System utilities required:** `curl` (for Telegram alerts, WHOIS IP lookup, `--test-whois`) and `whois` (for ISP verification, `--test-whois`)
