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

def main():
    # Parse command-line arguments
    parser = argparse.ArgumentParser(description="Check network interface connectivity and send alerts.")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose logging")
    args = parser.parse_args()

    verbose = args.verbose

    # Configuration
    interfaces = {
        'eth0': 'Primary Connection',
        'wlan0': 'Wi-Fi Connection'
    }  # Interfaces with custom labels
    websites = ['one.one.one.one', 'google.com']  # Websites to test
    sender_email = "your_email@example.com"
    recipient_email = "recipient_email@example.com"
    smtp_server = "smtp.example.com"
    smtp_port = 465
    smtp_login = "your_email@example.com"
    smtp_password = "your_password"
    use_ssl = True  # Use SSL for SMTP
    email_timeout = 10  # Timeout for sending emails
    state_file = "interface_states.csv"

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
                sender_email,
                recipient_email,
                subject,
                body,
                smtp_server,
                smtp_port,
                smtp_login,
                smtp_password,
                use_ssl=use_ssl,
                timeout=email_timeout
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
