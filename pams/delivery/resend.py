"""Resend REST API email transport."""

import base64
import json
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from pams.application.send_daily_report import EmailEnvelope

RESEND_EMAILS_URL = "https://api.resend.com/emails"


class ResendEmailError(RuntimeError):
    """Resend rejected or could not complete an email request."""


class ResendEmailTransport:
    """Send plain-text and HTML email through the official Resend REST API."""

    def __init__(
        self,
        api_key: str,
        *,
        endpoint: str = RESEND_EMAILS_URL,
        timeout_seconds: float = 30,
        opener: Callable[..., Any] = urlopen,
    ) -> None:
        self._api_key = api_key
        self._endpoint = endpoint
        self._timeout_seconds = timeout_seconds
        self._opener = opener

    def send(self, envelope: EmailEnvelope) -> None:
        """POST one email containing both text and HTML representations."""
        message = {
            "from": envelope.sender,
            "to": [envelope.recipient],
            "subject": envelope.subject,
            "text": envelope.plain_text,
            "html": envelope.html,
        }
        if envelope.inline_images:
            message["attachments"] = [
                {
                    "content": base64.b64encode(image.content).decode("ascii"),
                    "filename": image.filename,
                    "contentId": image.content_id,
                }
                for image in envelope.inline_images
            ]
        payload = json.dumps(message).encode("utf-8")
        request = Request(
            self._endpoint,
            data=payload,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "User-Agent": "PAMS/1.0",
            },
            method="POST",
        )
        try:
            with self._opener(request, timeout=self._timeout_seconds) as response:
                status = (
                    response.status
                    if hasattr(response, "status")
                    else response.getcode()
                )
                if not 200 <= status < 300:
                    raise ResendEmailError(f"Resend email API returned HTTP {status}")
        except HTTPError as error:
            raise ResendEmailError(
                f"Resend email API returned HTTP {error.code}"
            ) from error
