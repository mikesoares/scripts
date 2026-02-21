import smtplib
import socket
import ssl
import argparse
import json
import os
import csv
import subprocess
import sys
from collections import namedtuple
from datetime import datetime
from email.mime.text import MIMEText


# ============================================================
# Configuration
# ============================================================

Interface = namedtuple('Interface', ['label', 'expected_org'])


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
            if not line or line.startswith('#'):
                continue
            if '=' not in line:
                continue
            key, _, value = line.partition('=')
            key = key.strip()
            # Strip optional surrounding quotes from the value
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            if key not in os.environ:
                os.environ[key] = value


def parse_interfaces(raw):
    """Parse INTERFACES env var: comma-separated 'name:label[:expected_org]' entries.

    Examples:
        'eth0:Primary Connection,wlan0:Wi-Fi Connection'
        'eth0:Primary:BELL CANADA,wlan0:Wi-Fi:STARLINK'
        'eth0'  (bare name — used as its own label, no expected org)

    Returns dict mapping interface name → Interface(label, expected_org).
    """
    result = {}
    for entry in raw.split(','):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split(':', 2)
        name = parts[0].strip()
        label = parts[1].strip() if len(parts) >= 2 else name
        expected_org = parts[2].strip() if len(parts) >= 3 else None
        result[name] = Interface(label=label, expected_org=expected_org or None)
    return result


def load_config():
    """Load configuration from environment variables.

    Returns a dict with all config values. SMTP and Telegram vars are
    optional — if missing, the corresponding alert channel is disabled
    but connectivity checks still run and state is still saved.
    """
    # Interfaces: comma-separated name:label[:expected_org] entries
    interfaces_raw = os.environ.get('INTERFACES', 'eth0:Primary Connection,wlan0:Wi-Fi Connection')
    interfaces = parse_interfaces(interfaces_raw)

    # Websites to test connectivity against
    websites_raw = os.environ.get('WEBSITES', 'one.one.one.one,google.com')
    websites = [w.strip() for w in websites_raw.split(',') if w.strip()]

    # SMTP configuration — all five are needed for email alerts
    smtp_vars = {
        'SMTP_SENDER': os.environ.get('SMTP_SENDER'),
        'SMTP_RECIPIENT': os.environ.get('SMTP_RECIPIENT'),
        'SMTP_SERVER': os.environ.get('SMTP_SERVER'),
        'SMTP_LOGIN': os.environ.get('SMTP_LOGIN'),
        'SMTP_PASSWORD': os.environ.get('SMTP_PASSWORD'),
    }
    missing_smtp = [k for k, v in smtp_vars.items() if not v]
    email_available = len(missing_smtp) == 0

    smtp_port = int(os.environ.get('SMTP_PORT', '465'))
    smtp_use_ssl = os.environ.get('SMTP_USE_SSL', 'true').lower() in ('true', '1', 'yes')
    smtp_timeout = int(os.environ.get('SMTP_TIMEOUT', '10'))

    # Telegram configuration — both are needed for Telegram alerts
    telegram_vars = {
        'TELEGRAM_BOT_TOKEN': os.environ.get('TELEGRAM_BOT_TOKEN'),
        'TELEGRAM_CHAT_ID': os.environ.get('TELEGRAM_CHAT_ID'),
    }
    missing_telegram = [k for k, v in telegram_vars.items() if not v]
    telegram_available = len(missing_telegram) == 0

    # WHOIS / ISP verification
    whois_enabled = os.environ.get('WHOIS_ENABLED', 'false').lower() in ('true', '1', 'yes')
    ip_lookup_url = os.environ.get('IP_LOOKUP_URL', 'https://api.ipify.org')

    state_file = os.environ.get('STATE_FILE', 'interface_states.csv')

    return {
        'interfaces': interfaces,
        'websites': websites,
        # SMTP
        'sender_email': smtp_vars['SMTP_SENDER'],
        'recipient_email': smtp_vars['SMTP_RECIPIENT'],
        'smtp_server': smtp_vars['SMTP_SERVER'],
        'smtp_port': smtp_port,
        'smtp_login': smtp_vars['SMTP_LOGIN'],
        'smtp_password': smtp_vars['SMTP_PASSWORD'],
        'use_ssl': smtp_use_ssl,
        'email_timeout': smtp_timeout,
        'email_available': email_available,
        'missing_smtp_vars': missing_smtp,
        # Telegram
        'telegram_bot_token': telegram_vars['TELEGRAM_BOT_TOKEN'],
        'telegram_chat_id': telegram_vars['TELEGRAM_CHAT_ID'],
        'telegram_available': telegram_available,
        'missing_telegram_vars': missing_telegram,
        # WHOIS
        'whois_available': whois_enabled,
        'ip_lookup_url': ip_lookup_url,
        # State
        'state_file': state_file,
    }


def resolve_feature_flags(config, args):
    """Merge env-var availability with CLI overrides.

    CLI flags use a three-state convention: None = use env default,
    True = force-enable, False = force-disable. Force-enabling a feature
    whose required env vars are missing is an error.
    """
    flags = {'dry_run': args.dry_run}

    # Each feature: (cli_override, env_available, missing_var_names, feature_name)
    features = [
        (args.email, config['email_available'], config['missing_smtp_vars'], 'email'),
        (args.telegram, config['telegram_available'], config['missing_telegram_vars'], 'telegram'),
        (args.whois, config['whois_available'], [], 'whois'),
    ]

    for cli_val, available, missing, name in features:
        if cli_val is None:
            # No CLI override — use env default
            flags[name] = available
        elif cli_val is True:
            # Force-enable — check that required vars are present
            if not available and missing:
                print(f"Error: --{name} requires missing env vars: {', '.join(missing)}", file=sys.stderr)
                sys.exit(1)
            flags[name] = True
        else:
            # Force-disable
            flags[name] = False

    return flags


# ============================================================
# State Persistence
# ============================================================

def load_state(state_file):
    """Load previous interface states from a CSV file.

    Validates that each row has exactly 2 columns with a valid status.
    If the CSV is malformed, returns empty state — effectively a first
    run, which reinitializes the file on the next save_state() call
    without triggering false alerts.
    """
    if not os.path.exists(state_file):
        return {}

    state = {}
    try:
        with open(state_file, mode='r') as file:
            reader = csv.reader(file)
            for row in reader:
                if len(row) != 2:
                    raise ValueError(f"expected 2 columns, got {len(row)}")
                interface, status = row
                if status not in ('up', 'down'):
                    raise ValueError(f"invalid status '{status}' for {interface}")
                state[interface] = status
    except (ValueError, csv.Error):
        # Malformed CSV — treat as first run so state gets reinitialized
        return {}
    return state


def save_state(state_file, state):
    """Save current interface states to a CSV file."""
    with open(state_file, mode='w', newline='') as file:
        writer = csv.writer(file)
        for interface, status in state.items():
            writer.writerow([interface, status])


# ============================================================
# Connectivity Checks
# ============================================================

def check_connectivity(interface, websites, verbose):
    """Check connectivity of a specific interface to a list of websites using SSL.

    Binds a socket to the interface via SO_BINDTODEVICE and attempts
    HTTPS connections. DNS resolution uses the system default route
    (not bound to the interface) — intentional, since we only care
    whether the interface can carry TCP traffic.

    Returns (successful: bool, failures: list[str]).
    """
    successful = False
    failures = []
    for website in websites:
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, interface.encode())
            sock.settimeout(5)

            context = ssl.create_default_context()
            host_ip = socket.gethostbyname(website)
            with context.wrap_socket(sock, server_hostname=website) as ssl_sock:
                ssl_sock.connect((host_ip, 443))
            successful = True
            if verbose:
                print(f"  Connected to {website} via {interface}.")
        except Exception as e:
            failures.append(f"{website}: {e}")
            if verbose:
                print(f"  Failed to connect to {website} via {interface}: {e}")
            # Close the raw socket on failure — wrap_socket may not have
            # been reached, so the context manager wouldn't clean it up.
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass

    return successful, failures


def _curl_request(url, method='GET', data=None, interface=None, timeout=10):
    """Make an HTTP request via curl, optionally bound to a network interface.

    Uses curl --interface for SO_BINDTODEVICE-equivalent routing. This
    keeps the script dependency-free (no requests library) while still
    supporting interface-bound HTTP for Telegram and WHOIS IP lookups.

    Returns (success: bool, response_body: str).
    """
    cmd = ['curl', '-s', '-S', '--max-time', str(timeout)]
    if interface:
        cmd += ['--interface', interface]
    if method == 'POST' and data is not None:
        cmd += ['-X', 'POST', '-H', 'Content-Type: application/json', '-d', json.dumps(data)]
    cmd.append(url)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5)
        return result.returncode == 0, result.stdout
    except subprocess.TimeoutExpired:
        return False, 'curl timed out'
    except FileNotFoundError:
        return False, 'curl not found — required for Telegram/WHOIS features'


def _run_whois(ip):
    """Run whois lookup on an IP and extract the organization name.

    Handles multiple registry formats: ARIN (OrgName), RIPE (org-name),
    and generic (Organization). Returns the org name string or None.
    """
    try:
        result = subprocess.run(['whois', ip], capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            return None
        for line in result.stdout.splitlines():
            lower = line.lower().strip()
            if lower.startswith(('orgname:', 'org-name:', 'organization:')):
                return line.split(':', 1)[1].strip()
        return None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def verify_isp(interface, expected_org, ip_lookup_url, verbose=False):
    """Verify an interface routes through the expected ISP via IP + WHOIS.

    Fetches the public IP through the specified interface, then runs a
    WHOIS lookup to compare the org name against the expected value.
    Uses case-insensitive substring matching to handle naming variations
    (e.g., "Bell Canada" vs "BELL CANADA" vs "Bell Canada Inc.").

    Returns True if verification passes or cannot be performed (graceful
    fallback). Returns False only on an explicit ISP mismatch.
    """
    success, public_ip = _curl_request(ip_lookup_url, interface=interface, timeout=10)
    if not success or not public_ip.strip():
        if verbose:
            print(f"  IP lookup failed for {interface} — skipping ISP verification")
        return True

    public_ip = public_ip.strip()
    if verbose:
        print(f"  Public IP for {interface}: {public_ip}")

    actual_org = _run_whois(public_ip)
    if actual_org is None:
        if verbose:
            print(f"  WHOIS lookup failed for {public_ip} — skipping ISP verification")
        return True

    match = expected_org.upper() in actual_org.upper()
    if verbose:
        if match:
            print(f"  ISP verified: {actual_org}")
        else:
            print(f"  ISP MISMATCH: expected '{expected_org}', got '{actual_org}'")
    return match


# ============================================================
# Interface-Bound SMTP
# ============================================================

class _BoundSMTP(smtplib.SMTP):
    """SMTP client that binds to a specific network interface via SO_BINDTODEVICE."""

    def __init__(self, interface, *args, **kwargs):
        self._bind_interface = interface
        super().__init__(*args, **kwargs)

    def _get_socket(self, host, port, timeout):
        if timeout is not None and not timeout:
            raise ValueError('Non-blocking socket (timeout=0) is not supported')
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE,
                        self._bind_interface.encode())
        sock.settimeout(timeout)
        sock.connect((host, port))
        return sock


class _BoundSMTP_SSL(smtplib.SMTP_SSL):
    """SMTP_SSL client that binds to a specific network interface via SO_BINDTODEVICE."""

    def __init__(self, interface, *args, **kwargs):
        self._bind_interface = interface
        super().__init__(*args, **kwargs)

    def _get_socket(self, host, port, timeout):
        if timeout is not None and not timeout:
            raise ValueError('Non-blocking socket (timeout=0) is not supported')
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE,
                        self._bind_interface.encode())
        sock.settimeout(timeout)
        sock.connect((host, port))
        sock = self.context.wrap_socket(sock, server_hostname=self._host)
        return sock


# ============================================================
# Notifications
# ============================================================

def _format_alert_body(new_failures, restored_interfaces):
    """Build the alert message body from state-change lists.

    Returns a plain-text string suitable for both email and Telegram.
    """
    parts = []
    if new_failures:
        parts.append("The following interfaces failed connectivity:\n\n" +
                      "\n\n".join(new_failures))
    if restored_interfaces:
        parts.append("The following interfaces have been restored:\n\n" +
                      "\n".join(restored_interfaces))
    return "\n\n".join(parts)


def _send_email(body, config, interface=None, verbose=False,
                subject='Network Interface Status Update'):
    """Send an email alert, optionally bound to a specific network interface."""
    msg = MIMEText(body)
    msg['From'] = config['sender_email']
    msg['To'] = config['recipient_email']
    msg['Subject'] = subject

    if config['use_ssl']:
        context = ssl.create_default_context()
        if interface:
            with _BoundSMTP_SSL(interface, host=config['smtp_server'], port=config['smtp_port'],
                                context=context, timeout=config['email_timeout']) as server:
                server.login(config['smtp_login'], config['smtp_password'])
                server.sendmail(config['sender_email'], config['recipient_email'], msg.as_string())
        else:
            with smtplib.SMTP_SSL(config['smtp_server'], config['smtp_port'],
                                  context=context, timeout=config['email_timeout']) as server:
                server.login(config['smtp_login'], config['smtp_password'])
                server.sendmail(config['sender_email'], config['recipient_email'], msg.as_string())
    else:
        if interface:
            with _BoundSMTP(interface, host=config['smtp_server'], port=config['smtp_port'],
                            timeout=config['email_timeout']) as server:
                server.starttls()
                server.login(config['smtp_login'], config['smtp_password'])
                server.sendmail(config['sender_email'], config['recipient_email'], msg.as_string())
        else:
            with smtplib.SMTP(config['smtp_server'], config['smtp_port'],
                              timeout=config['email_timeout']) as server:
                server.starttls()
                server.login(config['smtp_login'], config['smtp_password'])
                server.sendmail(config['sender_email'], config['recipient_email'], msg.as_string())

    if verbose:
        print("Email alert sent successfully.")


def _send_telegram(body, config, interface=None, verbose=False,
                   subject='Network Interface Status Update'):
    """Send a Telegram alert via bot API, optionally bound to a network interface.

    Uses curl --interface for interface-bound routing, keeping the script
    free of external Python dependencies.
    """
    url = f"https://api.telegram.org/bot{config['telegram_bot_token']}/sendMessage"
    text = f"*{subject}*\n{body}"
    data = {
        'chat_id': config['telegram_chat_id'],
        'text': text,
        'parse_mode': 'Markdown',
    }

    success, response = _curl_request(url, method='POST', data=data, interface=interface)

    # Validate Telegram API response — curl exits 0 even on API errors
    # (e.g., bad token, invalid chat_id, Markdown parse failure)
    if success:
        try:
            result = json.loads(response)
            if not result.get('ok'):
                success = False
                response = result.get('description', response)
        except (json.JSONDecodeError, TypeError):
            pass  # Non-JSON response — trust curl's exit code

    if verbose:
        if success:
            print("Telegram alert sent successfully.")
        else:
            print(f"Failed to send Telegram alert: {response}")
    return success, response


def notify(new_failures, restored_interfaces, working_interface, config, flags, verbose=False):
    """Dispatch alerts to all enabled notification channels.

    Routes notifications through the working interface when available,
    falling back to unbound (OS-chosen route) when no interface is up.
    """
    body = _format_alert_body(new_failures, restored_interfaces)

    if flags['email']:
        if verbose:
            iface_msg = f" via {working_interface}" if working_interface else " (unbound)"
            print(f"Sending email alert{iface_msg}...")
        try:
            _send_email(body, config, interface=working_interface, verbose=verbose)
        except Exception as e:
            if verbose:
                print(f"Failed to send email alert: {e}")

    if flags['telegram']:
        if verbose:
            iface_msg = f" via {working_interface}" if working_interface else " (unbound)"
            print(f"Sending Telegram alert{iface_msg}...")
        _send_telegram(body, config, interface=working_interface, verbose=verbose)


# ============================================================
# CLI
# ============================================================

def build_parser():
    """Build the argument parser with all CLI flags."""
    parser = argparse.ArgumentParser(
        description="Check network interface connectivity and send alerts.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
features:
  Each feature is auto-enabled when its required env vars are set.
  CLI flags override the env-var defaults for a single invocation.

  email      Alerts via SMTP. Requires SMTP_SENDER, SMTP_RECIPIENT,
             SMTP_SERVER, SMTP_LOGIN, SMTP_PASSWORD.
  telegram   Alerts via Telegram Bot API. Requires TELEGRAM_BOT_TOKEN,
             TELEGRAM_CHAT_ID. Uses curl for interface-bound HTTP.
  whois      ISP verification via IP lookup + WHOIS after connectivity
             passes. Requires WHOIS_ENABLED=true and expected org in
             INTERFACES (name:label:expected_org). Uses curl and whois.

configuration:
  All settings come from environment variables. See .env.example for
  the full list and defaults. The script reads a .env file automatically
  (from the same directory as the script, or specify --env-file).

examples:
  python check_connectivity.py -v              Verbose check
  python check_connectivity.py --dry-run -v    Check without side effects
  python check_connectivity.py --show-config   Print effective config
  python check_connectivity.py --no-whois      Disable WHOIS for this run
  python check_connectivity.py --telegram --no-email  Telegram only
  python check_connectivity.py --test-alerts           Test notification channels
  python check_connectivity.py --test-whois            Test WHOIS / ISP lookup
  python check_connectivity.py --env-file /etc/connectivity.env""")
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='Enable verbose logging')
    parser.add_argument('--env-file', metavar='PATH', default=None,
                        help='Path to .env file (default: .env next to script)')

    # Feature toggle flags — None means "use env default"
    email_group = parser.add_mutually_exclusive_group()
    email_group.add_argument('--email', dest='email', action='store_true', default=None,
                             help='Force-enable email alerts')
    email_group.add_argument('--no-email', dest='email', action='store_false',
                             help='Disable email alerts')

    telegram_group = parser.add_mutually_exclusive_group()
    telegram_group.add_argument('--telegram', dest='telegram', action='store_true', default=None,
                                help='Force-enable Telegram alerts')
    telegram_group.add_argument('--no-telegram', dest='telegram', action='store_false',
                                help='Disable Telegram alerts')

    whois_group = parser.add_mutually_exclusive_group()
    whois_group.add_argument('--whois', dest='whois', action='store_true', default=None,
                             help='Force-enable WHOIS ISP verification')
    whois_group.add_argument('--no-whois', dest='whois', action='store_false',
                             help='Disable WHOIS ISP verification')

    parser.add_argument('--dry-run', action='store_true',
                        help='Check connectivity but do not save state or send alerts')
    parser.add_argument('--show-config', action='store_true',
                        help='Print effective configuration and exit')
    parser.add_argument('--test-alerts', action='store_true',
                        help='Send a test message through enabled alert channels and exit')
    parser.add_argument('--test-whois', action='store_true',
                        help='Fetch public IP and run WHOIS lookup to test ISP verification')

    return parser


def show_config(config, flags):
    """Print effective configuration to stdout and exit."""
    print("Interfaces:")
    for name, info in config['interfaces'].items():
        org = f"  (WHOIS: {info.expected_org})" if info.expected_org else ""
        print(f"  {name:<10} {info.label}{org}")

    print(f"\nWebsites: {', '.join(config['websites'])}")
    print(f"State file: {config['state_file']}")

    print("\nFeatures:")

    # Email
    if flags['email']:
        ssl_label = "SSL" if config['use_ssl'] else "STARTTLS"
        print(f"  Email:    enabled ({config['smtp_server']}:{config['smtp_port']} {ssl_label})")
    else:
        reason = f"missing {', '.join(config['missing_smtp_vars'])}" if config['missing_smtp_vars'] else "disabled via flag"
        print(f"  Email:    disabled ({reason})")

    # Telegram
    if flags['telegram']:
        # Redact token: show first 4 and last 4 chars
        token = config['telegram_bot_token'] or ''
        redacted = f"{token[:4]}...{token[-4:]}" if len(token) > 8 else '****'
        print(f"  Telegram: enabled (token: {redacted}, chat: {config['telegram_chat_id']})")
    else:
        reason = f"missing {', '.join(config['missing_telegram_vars'])}" if config['missing_telegram_vars'] else "disabled via flag"
        print(f"  Telegram: disabled ({reason})")

    # WHOIS
    if flags['whois']:
        print(f"  WHOIS:    enabled (IP lookup: {config['ip_lookup_url']})")
    else:
        print(f"  WHOIS:    disabled")


def test_alerts(config, flags):
    """Send a test message through all enabled notification channels.

    Prints results (success/failure) for each channel regardless of
    verbose mode. Returns True if all enabled channels succeeded.
    """
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    body = (
        f"This is a test alert.\n"
        f"Timestamp: {timestamp}\n\n"
        f"If you received this message, the notification channel is "
        f"configured correctly."
    )

    if not flags['email'] and not flags['telegram']:
        print("Error: No notification channels are enabled. "
              "Configure email or Telegram env vars, or use "
              "--email / --telegram to force-enable.", file=sys.stderr)
        return False

    all_ok = True

    if flags['email']:
        print(f"Testing email \u2192 {config['recipient_email']} "
              f"via {config['smtp_server']}:{config['smtp_port']}...")
        try:
            _send_email(body, config, interface=None, verbose=False,
                        subject='Test Alert \u2014 check_connectivity.py')
            print("  Email: OK")
        except Exception as e:
            print(f"  Email: FAILED \u2014 {e}", file=sys.stderr)
            all_ok = False

    if flags['telegram']:
        token = config['telegram_bot_token'] or ''
        redacted = f"{token[:4]}...{token[-4:]}" if len(token) > 8 else '****'
        print(f"Testing Telegram \u2192 chat {config['telegram_chat_id']} "
              f"(token: {redacted})...")
        success, response = _send_telegram(body, config, interface=None, verbose=False,
                                           subject='Test Alert')
        if success:
            print("  Telegram: OK")
        else:
            print(f"  Telegram: FAILED \u2014 {response}", file=sys.stderr)
            all_ok = False

    return all_ok


def test_whois(config):
    """Fetch public IP and run WHOIS to test ISP verification.

    Uses the OS default route (no interface binding). Shows the public IP,
    the WHOIS org found, and whether it matches each interface's expected org.
    Returns True if both IP lookup and WHOIS succeeded.
    """
    print(f"Fetching public IP via {config['ip_lookup_url']}...")
    success, public_ip = _curl_request(config['ip_lookup_url'], interface=None, timeout=10)
    if not success or not public_ip.strip():
        print(f"  IP lookup: FAILED \u2014 {public_ip}", file=sys.stderr)
        return False

    public_ip = public_ip.strip()
    print(f"  Public IP: {public_ip}")

    print(f"\nRunning WHOIS for {public_ip}...")
    actual_org = _run_whois(public_ip)
    if actual_org is None:
        print("  WHOIS: FAILED \u2014 could not determine organization", file=sys.stderr)
        return False

    print(f"  WHOIS org: {actual_org}")

    # Show match status for each interface with an expected org
    has_expected = any(info.expected_org for info in config['interfaces'].values())
    if has_expected:
        print("\nInterface matches:")
        for name, info in config['interfaces'].items():
            if info.expected_org:
                match = info.expected_org.upper() in actual_org.upper()
                status = "matches" if match else "MISMATCH"
                print(f"  {name} ({info.label}): expected \"{info.expected_org}\" \u2014 {status}")
            else:
                print(f"  {name} ({info.label}): no expected org configured")

    return True


# ============================================================
# Main
# ============================================================

def main():
    parser = build_parser()
    args = parser.parse_args()
    verbose = args.verbose

    # Load .env file — defaults to .env next to the script
    env_path = args.env_file or os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
    load_dotenv(env_path)

    config = load_config()
    flags = resolve_feature_flags(config, args)

    if args.show_config:
        show_config(config, flags)
        return

    if args.test_alerts:
        success = test_alerts(config, flags)
        sys.exit(0 if success else 1)

    if args.test_whois:
        success = test_whois(config)
        sys.exit(0 if success else 1)

    interfaces = config['interfaces']
    websites = config['websites']
    state_file = config['state_file']

    if verbose:
        disabled = [f for f in ('email', 'telegram', 'whois') if not flags[f]]
        enabled = [f for f in ('email', 'telegram', 'whois') if flags[f]]
        if enabled:
            print(f"Features enabled: {', '.join(enabled)}")
        if disabled:
            print(f"Features disabled: {', '.join(disabled)}")
        if flags['dry_run']:
            print("Dry run — state will not be saved and alerts will not be sent.")
        print()

    # Load previous state
    previous_state = load_state(state_file)
    current_state = {}
    new_failures = []
    restored_interfaces = []
    working_interface = None

    # Check each interface
    for interface, info in interfaces.items():
        if verbose:
            print(f"Testing {info.label} ({interface})...")

        successful, failures = check_connectivity(interface, websites, verbose)

        # Optional WHOIS/ISP verification — only if connectivity passed
        if successful and flags['whois'] and info.expected_org:
            if verbose:
                print(f"  Verifying ISP for {interface}...")
            if not verify_isp(interface, info.expected_org, config['ip_lookup_url'], verbose):
                successful = False
                failures.append(f"ISP mismatch (expected {info.expected_org})")

        current_state[interface] = "up" if successful else "down"

        if successful:
            working_interface = interface
            if previous_state.get(interface) == "down":
                restored_interfaces.append(info.label)
        else:
            if verbose:
                print(f"  {info.label} ({interface}) is DOWN.")
            if previous_state.get(interface) == "up":
                new_failures.append(f"{info.label} ({interface}):\n  " + "\n  ".join(failures))

    # Save state (unless dry run)
    if not flags['dry_run']:
        save_state(state_file, current_state)
    elif verbose:
        print("\nDry run — skipping state save.")

    # Send alerts on state changes
    state_changed = bool(new_failures or restored_interfaces)

    if state_changed and not flags['dry_run']:
        notify(new_failures, restored_interfaces, working_interface, config, flags, verbose)
    elif state_changed and flags['dry_run']:
        if verbose:
            print("\nDry run — would send the following alert:")
            print(_format_alert_body(new_failures, restored_interfaces))
    else:
        if verbose:
            print("\nNo state changes detected. No alerts sent.")


if __name__ == "__main__":
    main()
