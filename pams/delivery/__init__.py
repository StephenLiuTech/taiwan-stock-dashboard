"""Email delivery adapters and daily-report renderers."""

from pams.delivery.microsoft_graph import (
    MicrosoftAuthorizationError,
    MicrosoftAuthorizationRequiredError,
    MicrosoftGraphAuthenticator,
    MicrosoftGraphEmailError,
    MicrosoftGraphEmailTransport,
)
from pams.delivery.rendering import DailyEmailReportRenderer
from pams.delivery.resend import ResendEmailError, ResendEmailTransport
from pams.delivery.smtp import SMTPEmailTransport

__all__ = [
    "DailyEmailReportRenderer",
    "MicrosoftAuthorizationError",
    "MicrosoftAuthorizationRequiredError",
    "MicrosoftGraphAuthenticator",
    "MicrosoftGraphEmailError",
    "MicrosoftGraphEmailTransport",
    "ResendEmailError",
    "ResendEmailTransport",
    "SMTPEmailTransport",
]
