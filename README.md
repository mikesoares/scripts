# Scripts

Collection of useful standalone scripts. Each script is self-contained with its own purpose and dependencies.

## Scripts

### check_connectivity.py

Network interface connectivity monitor. Checks whether configured network interfaces can reach external websites over HTTPS, tracks state changes, and sends email alerts when interfaces go down or recover.

**Requirements:** Python 3.6+, Linux (`SO_BINDTODEVICE`), root or `CAP_NET_RAW`

**Usage:**

```bash
python check_connectivity.py       # Silent (for cron)
python check_connectivity.py -v    # Verbose output
```

**How it works:**

1. Tests each configured interface by attempting HTTPS connections to test websites
2. Compares current state against the previous state in `interface_states.csv`
3. If any interface changed state (up→down or down→up), sends an email alert via a working interface
4. Saves current state for the next run

On the first run (or if the state file is corrupted), state is stored without sending alerts.

**Configuration:** All settings are read from environment variables. Copy `.env.example` to `.env` and fill in your values. See `.env.example` for the full list of variables and their defaults.

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `INTERFACES` | No | `eth0:Primary Connection,wlan0:Wi-Fi Connection` | Comma-separated `name:label` pairs |
| `WEBSITES` | No | `one.one.one.one,google.com` | Comma-separated hostnames to test |
| `SMTP_SENDER` | For email | — | Sender email address |
| `SMTP_RECIPIENT` | For email | — | Recipient email address |
| `SMTP_SERVER` | For email | — | SMTP server hostname |
| `SMTP_LOGIN` | For email | — | SMTP login username |
| `SMTP_PASSWORD` | For email | — | SMTP password |
| `SMTP_PORT` | No | `465` | SMTP port |
| `SMTP_USE_SSL` | No | `true` | Use SSL for SMTP (`true`/`false`) |
| `SMTP_TIMEOUT` | No | `10` | SMTP connection timeout in seconds |
| `STATE_FILE` | No | `interface_states.csv` | Path to the state persistence file |

If any of the five required SMTP variables are missing, the script still runs connectivity checks and saves state — it just skips email alerts.

**Deployment:** Copy to the target server with a `.env` file and add a cron entry. Source the `.env` file to make variables available:

```
*/5 * * * * set -a && . /path/to/.env && set +a && /usr/bin/python3 /path/to/check_connectivity.py
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
