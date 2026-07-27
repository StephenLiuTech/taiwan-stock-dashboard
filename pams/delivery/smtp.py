"""Authenticated STARTTLS SMTP email transport."""

import smtplib
import ssl

from pams.application.send_daily_report import EmailEnvelope
from pams.delivery.mime import build_email_message


class SMTPEmailTransport:
    """Send multipart email without exposing authentication secrets."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        *,
        timeout_seconds: float = 30,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._timeout_seconds = timeout_seconds

    def send(self, envelope: EmailEnvelope) -> None:
        """Send one plain-text plus HTML message over STARTTLS."""
        message = build_email_message(envelope)
        with smtplib.SMTP(
            self._host, self._port, timeout=self._timeout_seconds
        ) as client:
            client.ehlo()
            client.starttls(context=ssl.create_default_context())
            client.ehlo()
            client.login(self._username, self._password)
            client.send_message(message)
