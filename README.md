# Scripts

Collection of useful standalone scripts. Each script is self-contained with its own purpose and dependencies.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Languages | Python 3 (varies per script) |
| License | GPL-3.0 |

## Scripts

### check_connectivity.py

Network interface connectivity monitor. Checks whether configured network interfaces can reach external websites over HTTPS, optionally verifies ISP routing via WHOIS, tracks state changes, and sends alerts (email and/or Telegram) when interfaces go down or recover.

**Requirements:** Python 3.6+, Linux (`SO_BINDTODEVICE`), root or `CAP_NET_RAW`. No external Python dependencies — uses only the standard library. Telegram and WHOIS features require `curl` and `whois` system utilities (`apt install curl whois`).

**Usage:**

```bash
python check_connectivity.py            # Silent (for cron)
python check_connectivity.py -v         # Verbose output
python check_connectivity.py --show-config  # Print effective config and exit
python check_connectivity.py --dry-run -v   # Check without saving state or alerting
python check_connectivity.py --no-whois -v  # Disable WHOIS for this run
python check_connectivity.py --telegram --no-email -v  # Telegram only
python check_connectivity.py --test-alerts  # Test notification channels and exit
python check_connectivity.py --test-alerts --no-email  # Test Telegram only
python check_connectivity.py --test-whois   # Test WHOIS / ISP lookup and exit
python check_connectivity.py --env-file /etc/connectivity.env -v  # Custom .env location
```

**CLI flags:**

| Flag | Description |
|------|-------------|
| `-v`, `--verbose` | Enable verbose logging to stdout |
| `--env-file PATH` | Path to `.env` file (default: `.env` next to script) |
| `--email` / `--no-email` | Force-enable/disable email alerts (default: auto from env) |
| `--telegram` / `--no-telegram` | Force-enable/disable Telegram alerts (default: auto from env) |
| `--whois` / `--no-whois` | Force-enable/disable WHOIS ISP verification (default: auto from env) |
| `--dry-run` | Check connectivity but don't save state or send alerts |
| `--show-config` | Print effective configuration and exit |
| `--test-alerts` | Send a test message through enabled alert channels and exit |
| `--test-whois` | Fetch public IP and run WHOIS lookup to test ISP verification |

**How it works:**

1. Loads `.env` file (from script directory or `--env-file` path), then parses CLI flags and merges with env vars
2. Iterates over configured network interfaces (e.g., `eth0`, `wlan0`)
3. For each interface, attempts HTTPS connections to test websites using `SO_BINDTODEVICE` to bind the socket
4. Optionally verifies ISP routing via IP lookup + WHOIS (if enabled and interface has an expected org configured)
5. Compares current state (up/down) against previous state in `interface_states.csv`
6. If any interface changed state (up→down or down→up), sends alerts via enabled channels (email, Telegram)
7. Saves current state for the next run

On the first run (or if the state file is corrupted), state is stored without sending alerts. Alerts only fire on state *transitions*.

**Configuration:** All settings are read from environment variables. Copy `.env.example` to `.env` (next to the script) and fill in your values. The script reads `.env` automatically via `load_dotenv()` — no shell sourcing needed. Use `--env-file` to point to a different location. Env vars already set in the shell take precedence over the file. CLI flags override feature toggles per-invocation.

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `INTERFACES` | No | `eth0:Primary Connection,wlan0:Wi-Fi Connection` | Comma-separated `name:label[:expected_org]` entries |
| `WEBSITES` | No | `one.one.one.one,google.com` | Comma-separated hostnames to test |
| `SMTP_SENDER` | For email | — | Sender email address |
| `SMTP_RECIPIENT` | For email | — | Recipient email address |
| `SMTP_SERVER` | For email | — | SMTP server hostname |
| `SMTP_LOGIN` | For email | — | SMTP login username |
| `SMTP_PASSWORD` | For email | — | SMTP password |
| `SMTP_PORT` | No | `465` | SMTP port |
| `SMTP_USE_SSL` | No | `true` | Use SSL for SMTP (`true`/`false`) |
| `SMTP_TIMEOUT` | No | `10` | SMTP connection timeout in seconds |
| `TELEGRAM_BOT_TOKEN` | For Telegram | — | Telegram bot API token |
| `TELEGRAM_CHAT_ID` | For Telegram | — | Telegram chat ID for alerts |
| `WHOIS_ENABLED` | No | `false` | Enable ISP verification via WHOIS |
| `IP_LOOKUP_URL` | No | `https://api.ipify.org` | Comma-separated URLs that return public IP as plain text. Tried in order; falls back to the next if one fails. |
| `STATE_FILE` | No | `interface_states.csv` | Absolute path to the state persistence file. Relative paths resolve from the working directory, not the script's directory — use an absolute path to avoid surprises with cron. |

Each alert channel (email, Telegram) is independently optional. If its required env vars are missing, the channel is disabled but connectivity checks still run and state is saved. `--dry-run` skips state saving and alert sending.

**Deployment:** Copy to the target server with a `.env` file next to the script and add a cron entry. Requires root privileges (or `CAP_NET_RAW` capability) for `SO_BINDTODEVICE`.

```
*/5 * * * * /usr/bin/python3 /path/to/check_connectivity.py
```

Or point to a `.env` file elsewhere:

```
*/5 * * * * /usr/bin/python3 /path/to/check_connectivity.py --env-file /etc/connectivity.env
```

Per-invocation overrides work in cron too:

```
*/5 * * * * /usr/bin/python3 /path/to/check_connectivity.py --no-whois
```

## Project Structure

```
scripts/
├── check_connectivity.py   # Network interface connectivity monitor
├── .env.example            # Environment variable template
├── interface_states.csv     # Runtime — last-known interface states (auto-created, gitignored)
├── LICENSE                  # GPL-3.0
├── README.md
└── TODOS.md
```

## License

GPL-3.0. See [LICENSE](LICENSE) for details.
