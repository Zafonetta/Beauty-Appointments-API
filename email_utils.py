#This file is the construction factory for all outbound emails in our application (like password reset links or welcome notifications).
from email.message import EmailMessage

import aiosmtplib  # securely connect to Mailtrap across the network and transmit the message data packets
from fastapi.templating import Jinja2Templates

from config import settings

# Initialize Jinja2 to look inside the "templates" folder for HTML email layouts
templates = Jinja2Templates(directory="templates")
#create sending email function
async def send_email(
    to_email: str,
    subject: str,
    plain_text: str,
    html_content: str | None = None,
) -> None:
    """
        Constructs and prepares a secure, dual-format email message container.
        Supports basic fallback plain text and optional rich HTML layouts.
        """
    message = EmailMessage() # 1. Initialize Python's standard electronic mail container
# 2. Populate the required standard envelope headers using your app configurations
    message["From"] = settings.mail_from
    message["To"] = to_email
    message["Subject"] = subject
# 3. Establish the base layer body (mandatory fallback for basic devices/smartwatches)
    message.set_content(plain_text)
# 4. If an advanced HTML design is provided, attach it as a preferred alternative view
    if html_content:
        message.add_alternative(html_content, subtype="html")

#part that sends email
    # Connect asynchronously to the SMTP server and send the compiled email message
    await aiosmtplib.send(
        message,  # The email container holding your subject, headers, text, and HTML
        hostname=settings.mail_server,  # The server domain address (e.g., sandbox.smtp.mailtrap.io)
        port=settings.mail_port,  # The explicit port number used for the connection (e.g., 2525)
        # Pass the username string if it exists; otherwise, default to None for anonymous servers
        username=settings.mail_username if settings.mail_username else None,
        # Safely unmask the Pydantic SecretStr to reveal the raw password for the login attempt
        password=settings.mail_password.get_secret_value() if settings.mail_password else None,
        # Enable TLS encryption standard to keep data safe while traveling over the wire
        start_tls=settings.mail_use_tls,
    )
async def send_password_reset_email(to_email: str, username: str, token: str) -> None:
    """
    Coordinates the password reset process by generating a verification URL,
    rendering an HTML message layout, and dispatching it to the target user.
    """
    # 1. Build the unique single-use password reset hyperlink
    reset_url = f"{settings.frontend_url}/reset-password?token={token}"

    # 2. Load the specific HTML layout and inject the dynamic user parameters
    template = templates.env.get_template("email/password_reset.html")
    html_content = template.render(reset_url=reset_url, username=username)

    # 3. Formulate the alternative raw plain text body variant
    plain_text = f"""Hi {username},

You requested to reset your password. Click the link below to set a new password:

{reset_url}

This link will expire in 1 hour.

If you didn't request this, you can safely ignore this email.

Best regards,
The FastAPI Blog Team
"""
    # 4. Forward all prepared elements to our underlying core email system
    await send_email(
        to_email=to_email,
        subject="Reset Your Password - FastAPI Blog",
        plain_text=plain_text,
        html_content=html_content,
    )