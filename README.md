# Scripts

Collection of useful standalone scripts. Each script is self-contained with its own purpose and dependencies.

## Scripts

### check_connectivity.py

Network interface connectivity monitor. Checks whether configured network interfaces can reach external websites over HTTPS, optionally verifies ISP routing via WHOIS, tracks state changes, and sends alerts (email and/or Telegram) when interfaces go down or recover.

**Requirements:** Python 3.6+, Linux (`SO_BINDTODEVICE`), root or `CAP_NET_RAW`. Telegram and WHOIS features require `curl` and `whois` (`apt install curl whois`).

**Usage:**

```bash
python check_connectivity.py            # Silent (for cron)
python check_connectivity.py -v         # Verbose output
python check_connectivity.py --show-config  # Print effective config and exit
python check_connectivity.py --dry-run -v   # Check without saving state or alerting
python check_connectivity.py --no-whois -v  # Disable WHOIS for this run
python check_connectivity.py --test-alerts  # Test notification channels and exit
python check_connectivity.py --test-whois   # Test WHOIS / ISP lookup and exit
```

**How it works:**

1. Tests each configured interface by attempting HTTPS connections to test websites
2. Optionally verifies ISP routing via IP lookup + WHOIS (if enabled)
3. Compares current state against the previous state in `interface_states.csv`
4. If any interface changed state (up→down or down→up), sends alerts via enabled channels (email, Telegram)
5. Saves current state for the next run

On the first run (or if the state file is corrupted), state is stored without sending alerts.

**Configuration:** All settings are read from environment variables. Copy `.env.example` to `.env` (next to the script) and fill in your values. The script reads `.env` automatically — no shell sourcing needed. Use `--env-file` to point to a different location. CLI flags override feature toggles per-invocation.

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
| `IP_LOOKUP_URL` | No | `https://api.ipify.org` | URL that returns public IP as plain text |
| `STATE_FILE` | No | `interface_states.csv` | Path to the state persistence file |

Each alert channel (email, Telegram) is independently optional. If its required env vars are missing, the channel is disabled but connectivity checks still run and state is saved. CLI flags (`--email`/`--no-email`, `--telegram`/`--no-telegram`, `--whois`/`--no-whois`) override per-invocation. `--dry-run` skips state saving and alert sending.

**Deployment:** Copy to the target server with a `.env` file next to the script and add a cron entry:

```
*/5 * * * * /usr/bin/python3 /path/to/check_connectivity.py
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
