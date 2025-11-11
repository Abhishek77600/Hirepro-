"""
Email sending helper using SMTP.
Configure MAIL_SERVER, MAIL_PORT, MAIL_USERNAME, MAIL_PASSWORD, and MAIL_DEFAULT_SENDER in env.
"""
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


def send_email(to_email, subject, body, html_body=None):
    """Send an email via SMTP. Returns True on success, raises on failure."""
    
    mail_server = os.getenv('MAIL_SERVER')
    mail_port = int(os.getenv('MAIL_PORT', 587))
    mail_username = os.getenv('MAIL_USERNAME')
    mail_password = os.getenv('MAIL_PASSWORD')
    sender = os.getenv('MAIL_DEFAULT_SENDER', mail_username)
    use_tls = os.getenv('MAIL_USE_TLS', 'True').lower() in ('true', '1', 'yes', 'on')

    # Validate required config
    if not mail_server or not mail_username or not mail_password:
        raise RuntimeError(
            'Email not configured. Set MAIL_SERVER, MAIL_USERNAME, MAIL_PASSWORD, '
            'MAIL_DEFAULT_SENDER env vars'
        )

    # Build message
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = sender
    msg['To'] = to_email
    msg.attach(MIMEText(body, 'plain'))
    if html_body:
        msg.attach(MIMEText(html_body, 'html'))

    # Send via SMTP
    try:
        with smtplib.SMTP(mail_server, mail_port, timeout=20) as server:
            server.ehlo()
            if use_tls:
                server.starttls()
                server.ehlo()
            server.login(mail_username, mail_password)
            server.send_message(msg)
        print(f"Email sent to {to_email} via SMTP")
        return True
    except Exception as e:
        print(f"SMTP send error: {e}")
        raise
