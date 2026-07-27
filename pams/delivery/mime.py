"""Shared MIME construction for transports that accept RFC email messages."""

from email.message import EmailMessage

from pams.application.send_daily_report import EmailEnvelope


def build_email_message(envelope: EmailEnvelope) -> EmailMessage:
    """Build multipart/alternative content with optional CID-related images."""
    message = EmailMessage()
    message["Subject"] = envelope.subject
    message["From"] = envelope.sender
    message["To"] = envelope.recipient
    message.set_content(envelope.plain_text)
    message.add_alternative(envelope.html, subtype="html")
    html_part = message.get_payload()[-1]
    for image in envelope.inline_images:
        maintype, subtype = image.content_type.split("/", maxsplit=1)
        html_part.add_related(
            image.content,
            maintype=maintype,
            subtype=subtype,
            cid=f"<{image.content_id}>",
            disposition="inline",
        )
    return message
