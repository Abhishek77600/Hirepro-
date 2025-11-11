"""
Email sending helper using SendGrid Web API.
Requires SENDGRID_API_KEY and MAIL_DEFAULT_SENDER environment variables.
"""
import os
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
from sendgrid.exceptions import SendGridException


def send_email(to_email, subject, body, html_body=None):
    """
    Send an email using SendGrid API.
    Returns True on success, raises exception on failure.
    
    Args:
        to_email: Recipient email address
        subject: Email subject
        body: Plain text email body
        html_body: Optional HTML email body
    
    Raises:
        RuntimeError: If SendGrid API key is not configured
        Exception: If email sending fails
    """
    sendgrid_key = os.getenv('SENDGRID_API_KEY')
    sender = os.getenv('MAIL_DEFAULT_SENDER')
    
    # Detailed validation with helpful error messages
    if not sendgrid_key:
        error_msg = 'SENDGRID_API_KEY environment variable is not set. Please set it in Render dashboard under Environment variables.'
        print(f"ERROR: {error_msg}")
        raise RuntimeError(error_msg)
    
    if not sender:
        error_msg = 'MAIL_DEFAULT_SENDER environment variable is not set. Please set it in Render dashboard under Environment variables.'
        print(f"ERROR: {error_msg}")
        raise RuntimeError(error_msg)
    
    # Validate API key format (should start with SG.)
    if not sendgrid_key.startswith('SG.'):
        error_msg = f'SENDGRID_API_KEY appears to be invalid. API keys should start with "SG." Got: {sendgrid_key[:10]}...'
        print(f"ERROR: {error_msg}")
        raise ValueError(error_msg)
    
    print(f"Attempting to send email via SendGrid:")
    print(f"  From: {sender}")
    print(f"  To: {to_email}")
    print(f"  Subject: {subject}")
    print(f"  API Key present: Yes (starts with SG.)")
    
    try:
        sg = SendGridAPIClient(sendgrid_key)
        message = Mail(
            from_email=sender,
            to_emails=to_email,
            subject=subject,
            plain_text_content=body
        )
        
        if html_body:
            message.html = html_body
        
        response = sg.send(message)
        
        # Check response status
        status_code = response.status_code
        print(f"SendGrid API response: status {status_code}")
        
        # SendGrid returns 202 for successful sends
        if status_code == 202:
            print(f"✓ Email sent successfully to {to_email}")
            return True
        else:
            # Try to get response body for error details
            try:
                response_body = response.body.decode('utf-8') if response.body else 'No response body'
                error_msg = f"SendGrid returned status {status_code}: {response_body}"
                print(f"ERROR: {error_msg}")
                raise Exception(error_msg)
            except:
                error_msg = f"SendGrid returned unexpected status code: {status_code}"
                print(f"ERROR: {error_msg}")
                raise Exception(error_msg)
                
    except SendGridException as e:
        # SendGrid-specific exceptions
        error_details = str(e)
        if hasattr(e, 'body') and e.body:
            try:
                import json
                error_body = json.loads(e.body) if isinstance(e.body, str) else e.body
                error_details = f"{error_details} - Details: {error_body}"
            except:
                error_details = f"{error_details} - Body: {e.body}"
        
        error_msg = f"SendGrid API error: {error_details}"
        print(f"ERROR: {error_msg}")
        print(f"  This usually means:")
        print(f"  1. API key is invalid or expired")
        print(f"  2. Sender email ({sender}) is not verified in SendGrid")
        print(f"  3. SendGrid account has restrictions")
        raise Exception(error_msg)
        
    except Exception as e:
        error_msg = f"Unexpected error sending email: {type(e).__name__}: {str(e)}"
        print(f"ERROR: {error_msg}")
        raise Exception(error_msg)
