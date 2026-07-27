"""Microsoft Graph delegated OAuth2 email transport."""

import base64
import importlib
import os
from collections.abc import Callable
from email.policy import SMTP
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.request import Request, urlopen

from pams.application.send_daily_report import EmailEnvelope
from pams.delivery.mime import build_email_message

GRAPH_SEND_MAIL_URL = "https://graph.microsoft.com/v1.0/me/sendMail"
GRAPH_SCOPES = ("Mail.Send",)


class MicrosoftAuthorizationRequiredError(RuntimeError):
    """No cached delegated token can be acquired silently."""


class MicrosoftAuthorizationError(RuntimeError):
    """Interactive device authorization failed."""


class MicrosoftGraphEmailError(RuntimeError):
    """Microsoft Graph rejected the sendMail request."""


class MicrosoftGraphAuthenticator:
    """Own a public-client MSAL application and its serialized token cache."""

    def __init__(
        self,
        client_id: str,
        tenant: str,
        cache_path: Path,
        *,
        msal_module: ModuleType | None = None,
    ) -> None:
        self._cache_path = cache_path
        self._msal = msal_module or importlib.import_module("msal")
        self._cache = self._msal.SerializableTokenCache()
        if cache_path.exists():
            self._cache.deserialize(cache_path.read_text(encoding="utf-8"))
        self._application = self._msal.PublicClientApplication(
            client_id,
            authority=f"https://login.microsoftonline.com/{tenant}",
            token_cache=self._cache,
        )

    def acquire_token_silent(self) -> str:
        """Return a cached/refreshed token without interactive authorization."""
        for account in self._application.get_accounts():
            result = self._application.acquire_token_silent(
                list(GRAPH_SCOPES), account=account
            )
            self._persist_cache()
            if result and result.get("access_token"):
                return str(result["access_token"])
        raise MicrosoftAuthorizationRequiredError(
            "Microsoft authorization is required; run "
            "'python -m pams email authorize'"
        )

    def authorize(self, show_prompt: Callable[[str, str], None]) -> None:
        """Complete the interactive device-code flow and persist its cache."""
        flow = self._application.initiate_device_flow(scopes=list(GRAPH_SCOPES))
        if "user_code" not in flow:
            detail = flow.get("error_description") or flow.get("error") or "unknown"
            raise MicrosoftAuthorizationError(
                f"could not initiate Microsoft device authorization: {detail}"
            )
        verification_uri = str(
            flow.get("verification_uri")
            or flow.get("verification_uri_complete")
            or "https://microsoft.com/devicelogin"
        )
        show_prompt(verification_uri, str(flow["user_code"]))
        result = self._application.acquire_token_by_device_flow(flow)
        self._persist_cache()
        if not result or "access_token" not in result:
            detail = (
                result.get("error_description")
                or result.get("error")
                or "authorization did not return an access token"
            )
            raise MicrosoftAuthorizationError(
                f"Microsoft device authorization failed: {detail}"
            )

    def _persist_cache(self) -> None:
        if not self._cache.has_state_changed:
            return
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        descriptor = os.open(self._cache_path, flags, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as cache_file:
            cache_file.write(self._cache.serialize())
        try:
            os.chmod(self._cache_path, 0o600)
        except OSError:
            # Windows ACL ownership remains the current user even when POSIX mode
            # emulation is unavailable.
            pass


class MicrosoftGraphEmailTransport:
    """Send multipart MIME email through delegated Graph sendMail."""

    def __init__(
        self,
        authenticator: MicrosoftGraphAuthenticator,
        *,
        endpoint: str = GRAPH_SEND_MAIL_URL,
        timeout_seconds: float = 30,
        opener: Callable[..., Any] = urlopen,
    ) -> None:
        self._authenticator = authenticator
        self._endpoint = endpoint
        self._timeout_seconds = timeout_seconds
        self._opener = opener

    def send(self, envelope: EmailEnvelope) -> None:
        """Acquire a token silently and send a MIME multipart/alternative body."""
        access_token = self._authenticator.acquire_token_silent()
        message = build_email_message(envelope)
        encoded_mime = base64.b64encode(message.as_bytes(policy=SMTP))
        request = Request(
            self._endpoint,
            data=encoded_mime,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "text/plain",
            },
            method="POST",
        )
        with self._opener(request, timeout=self._timeout_seconds) as response:
            status = (
                response.status if hasattr(response, "status") else response.getcode()
            )
            if status != 202:
                raise MicrosoftGraphEmailError(
                    f"Microsoft Graph sendMail returned HTTP {status}"
                )
