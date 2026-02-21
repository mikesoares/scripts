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
3. Sends an email alert if any interface changed state (up→down or down→up)
4. Saves current state for the next run

**Configuration:** All settings (interfaces, SMTP, test websites) are currently hardcoded in `main()`.

**Deployment:** Copy to the target server and add a cron entry:

```
*/5 * * * * /usr/bin/python3 /path/to/check_connectivity.py
```

## Project Structure

```
scripts/
├── check_connectivity.py   # Network interface connectivity monitor
├── interface_states.csv     # Runtime — last-known interface states (auto-created, gitignored)
├── LICENSE                  # GPL-3.0
├── README.md
└── TODOS.md
```

## License

GPL-3.0. See [LICENSE](LICENSE) for details.
