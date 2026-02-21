import smtplib
import socket
import ssl
import argparse
import os
import csv
from email.mime.text import MIMEText


def check_connectivity(interface, websites, verbose):
    """Check connectivity of a specific interface to a list of websites using SSL."""
    successful = False
    failures = []
    for website in websites:
        try:
            # Create a socket and bind it to the specific interface
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, interface.encode())
            sock.settimeout(5)  # 5-second timeout

            # Resolve the website and attempt a connection via SSL
            context = ssl.create_default_context()
            host_ip = socket.gethostbyname(website)
            with context.wrap_socket(sock, server_hostname=website) as ssl_sock:
                ssl_sock.connect((host_ip, 443))  # HTTPS port
                ssl_sock.close()
            successful = True
            if verbose:
                print(f"Successfully connected to {website} via {interface}.")
        except Exception as e:
            failures.append(f"{website}: {e}")
            if verbose:
                print(f"Failed to connect to {website} via {interface}: {e}")

    return successful, failures


def send_email(sender_email, recipient_email, subject, body, smtp_server, smtp_port, login, password, use_ssl=False, timeout=10):
    """Send an email notification."""
    msg = MIMEText(body)
    msg['From'] = sender_email
    msg['To'] = recipient_email
    msg['Subject'] = subject

    if use_ssl:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(smtp_server, smtp_port, context=context, timeout=timeout) as server:
            server.login(login, password)
            server.sendmail(sender_email, recipient_email, msg.as_string())
    else:
        with smtplib.SMTP(smtp_server, smtp_port, timeout=timeout) as server:
            server.starttls()
            server.login(login, password)
            server.sendmail(sender_email, recipient_email, msg.as_string())


def load_state(state_file):
    """Load previous interface states from a CSV file."""
    if not os.path.exists(state_file):
        return {}

    state = {}
    with open(state_file, mode='r') as file:
        reader = csv.reader(file)
        for row in reader:
            interface, status = row
            state[interface] = status
    return state


def save_state(state_file, state):
    """Save current interface states to a CSV file."""
    with open(state_file, mode='w', newline='') as file:
        writer = csv.writer(file)
        for interface, status in state.items():
            writer.writerow([interface, status])


def parse_interfaces(raw):
    """Parse INTERFACES env var: comma-separated 'name:label' pairs.

    Example: 'eth0:Primary Connection,wlan0:Wi-Fi Connection'
    If no label is provided for an entry, the interface name is used as the label.
    """
    result = {}
    for entry in raw.split(','):
        entry = entry.strip()
        if not entry:
            continue
        if ':' in entry:
            name, label = entry.split(':', 1)
            result[name.strip()] = label.strip()
        else:
            result[entry.strip()] = entry.strip()
    return result


def load_config():
    """Load configuration from environment variables.

    Returns a dict with all config values. SMTP vars are optional — if any
    are missing, email alerts are disabled and the script still runs
    connectivity checks and saves state.
    """
    # Interfaces: comma-separated name:label pairs
    interfaces_raw = os.environ.get('INTERFACES', 'eth0:Primary Connection,wlan0:Wi-Fi Connection')
    interfaces = parse_interfaces(interfaces_raw)

    # Websites to test connectivity against
    websites_raw = os.environ.get('WEBSITES', 'one.one.one.one,google.com')
    websites = [w.strip() for w in websites_raw.split(',') if w.strip()]

    # SMTP configuration — all five are needed for email alerts to work
    smtp_vars = {
        'SMTP_SENDER': os.environ.get('SMTP_SENDER'),
        'SMTP_RECIPIENT': os.environ.get('SMTP_RECIPIENT'),
        'SMTP_SERVER': os.environ.get('SMTP_SERVER'),
        'SMTP_LOGIN': os.environ.get('SMTP_LOGIN'),
        'SMTP_PASSWORD': os.environ.get('SMTP_PASSWORD'),
    }
    missing = [k for k, v in smtp_vars.items() if not v]
    email_enabled = len(missing) == 0

    smtp_port = int(os.environ.get('SMTP_PORT', '465'))
    smtp_use_ssl = os.environ.get('SMTP_USE_SSL', 'true').lower() in ('true', '1', 'yes')
    smtp_timeout = int(os.environ.get('SMTP_TIMEOUT', '10'))

    state_file = os.environ.get('STATE_FILE', 'interface_states.csv')

    return {
        'interfaces': interfaces,
        'websites': websites,
        'sender_email': smtp_vars['SMTP_SENDER'],
        'recipient_email': smtp_vars['SMTP_RECIPIENT'],
        'smtp_server': smtp_vars['SMTP_SERVER'],
        'smtp_port': smtp_port,
        'smtp_login': smtp_vars['SMTP_LOGIN'],
        'smtp_password': smtp_vars['SMTP_PASSWORD'],
        'use_ssl': smtp_use_ssl,
        'email_timeout': smtp_timeout,
        'state_file': state_file,
        'email_enabled': email_enabled,
        'missing_smtp_vars': missing,
    }


def main():
    # Parse command-line arguments
    parser = argparse.ArgumentParser(description="Check network interface connectivity and send alerts.")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose logging")
    args = parser.parse_args()

    verbose = args.verbose

    # Load configuration from environment variables
    config = load_config()
    interfaces = config['interfaces']
    websites = config['websites']
    state_file = config['state_file']

    if not config['email_enabled'] and verbose:
        print(f"Warning: Email alerts disabled — missing env vars: {', '.join(config['missing_smtp_vars'])}")

    # Load previous states
    previous_state = load_state(state_file)
    current_state = {}

    new_failures = []
    restored_interfaces = []
    working_interface = None

    # Check each interface's connectivity
    for interface, label in interfaces.items():
        if verbose:
            print(f"Testing connectivity for interface {label} ({interface})...")
        successful, failures = check_connectivity(interface, websites, verbose)
        current_state[interface] = "up" if successful else "down"

        if successful:
            if verbose:
                print(f"Interface {label} ({interface}) is working.")
            working_interface = interface  # Update to the last working interface
            if previous_state.get(interface) == "down":
                restored_interfaces.append(label)
        else:
            if verbose:
                print(f"Interface {label} ({interface}) failed.")
            if previous_state.get(interface) == "up":
                new_failures.append(f"{label} ({interface}):\n  " + "\n  ".join(failures))

    # Save current state
    save_state(state_file, current_state)

    # Prepare and send email if state changes
    if new_failures or restored_interfaces:
        subject = "Network Interface Status Update"
        body = ""

        if new_failures:
            body += f"The following interfaces failed connectivity:\n\n" + "\n\n".join(new_failures) + "\n\n"

        if restored_interfaces:
            body += f"The following interfaces have been restored:\n\n" + "\n".join(restored_interfaces) + "\n\n"

        if not config['email_enabled']:
            if verbose:
                print("State changes detected but email alerts are disabled (missing SMTP config).")
        else:
            if verbose:
                print("Preparing to send alert email...")

            if working_interface:
                if verbose:
                    print(f"Using working interface {working_interface} to send email.")
            else:
                if verbose:
                    print("No working interface detected. Attempting to send email regardless.")

            try:
                send_email(
                    config['sender_email'],
                    config['recipient_email'],
                    subject,
                    body,
                    config['smtp_server'],
                    config['smtp_port'],
                    config['smtp_login'],
                    config['smtp_password'],
                    use_ssl=config['use_ssl'],
                    timeout=config['email_timeout']
                )
                if verbose:
                    print("Alert email sent successfully.")
            except Exception as e:
                if verbose:
                    print(f"Failed to send alert email: {e}")
    else:
        if verbose:
            print("No state changes detected. No email sent.")


if __name__ == "__main__":
    main()
