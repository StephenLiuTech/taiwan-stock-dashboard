"""Offline tests for delegated Microsoft Graph email delivery."""

import base64
from email import policy
from email.parser import BytesParser
from pathlib import Path
from types import SimpleNamespace
from urllib.request import Request

import pytest

from pams.application.send_daily_report import EmailEnvelope, InlineImage
from pams.delivery import (
    MicrosoftAuthorizationRequiredError,
    MicrosoftGraphAuthenticator,
    MicrosoftGraphEmailError,
    MicrosoftGraphEmailTransport,
)


class FakeCache:
    def __init__(self) -> None:
        self.has_state_changed = False
        self.serialized = ""

    def deserialize(self, serialized: str) -> None:
        self.serialized = serialized

    def serialize(self) -> str:
        return '{"TokenCache":"opaque"}'


class FakeApplication:
    def __init__(self, *, accounts: list[object] | None = None) -> None:
        self.accounts = accounts or []
        self.scopes: list[str] = []

    def get_accounts(self) -> list[object]:
        return self.accounts

    def acquire_token_silent(
        self, scopes: list[str], *, account: object
    ) -> dict[str, str] | None:
        assert account in self.accounts
        self.scopes = scopes
        return {"access_token": "silent-access-token"}

    def initiate_device_flow(self, *, scopes: list[str]) -> dict[str, str]:
        self.scopes = scopes
        return {
            "user_code": "ABCD-EFGH",
            "verification_uri": "https://microsoft.com/devicelogin",
        }

    def acquire_token_by_device_flow(self, flow: dict[str, str]) -> dict[str, str]:
        assert flow["user_code"] == "ABCD-EFGH"
        return {
            "access_token": "interactive-access-token",
            "refresh_token": "refresh-token-must-not-be-printed",
        }


def fake_msal(application: FakeApplication, cache: FakeCache) -> SimpleNamespace:
    return SimpleNamespace(
        SerializableTokenCache=lambda: cache,
        PublicClientApplication=lambda *_args, **_kwargs: application,
    )


def test_device_authorization_displays_safe_prompt_and_persists_cache(
    tmp_path: Path,
) -> None:
    cache = FakeCache()
    cache.has_state_changed = True
    application = FakeApplication()
    prompts: list[tuple[str, str]] = []
    cache_path = tmp_path / "tokens" / "msal.json"
    authenticator = MicrosoftGraphAuthenticator(
        "client-id",
        "consumers",
        cache_path,
        msal_module=fake_msal(application, cache),  # type: ignore[arg-type]
    )

    authenticator.authorize(lambda url, code: prompts.append((url, code)))

    assert application.scopes == ["Mail.Send"]
    assert prompts == [("https://microsoft.com/devicelogin", "ABCD-EFGH")]
    assert cache_path.read_text(encoding="utf-8") == '{"TokenCache":"opaque"}'
    assert "interactive-access-token" not in repr(prompts)
    assert "refresh-token-must-not-be-printed" not in repr(prompts)


def test_silent_token_acquisition_uses_cached_account(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache.json"
    cache_path.write_text('{"existing":"cache"}', encoding="utf-8")
    cache = FakeCache()
    application = FakeApplication(accounts=[object()])
    authenticator = MicrosoftGraphAuthenticator(
        "client-id",
        "consumers",
        cache_path,
        msal_module=fake_msal(application, cache),  # type: ignore[arg-type]
    )

    assert authenticator.acquire_token_silent() == "silent-access-token"
    assert cache.serialized == '{"existing":"cache"}'
    assert application.scopes == ["Mail.Send"]


def test_missing_cached_account_requires_explicit_authorization(
    tmp_path: Path,
) -> None:
    authenticator = MicrosoftGraphAuthenticator(
        "client-id",
        "consumers",
        tmp_path / "cache.json",
        msal_module=fake_msal(FakeApplication(), FakeCache()),  # type: ignore[arg-type]
    )

    with pytest.raises(
        MicrosoftAuthorizationRequiredError,
        match="python -m pams email authorize",
    ):
        authenticator.acquire_token_silent()


def test_graph_transport_posts_multipart_alternative_mime() -> None:
    requests: list[tuple[Request, float]] = []

    class Authenticator:
        def acquire_token_silent(self) -> str:
            return "opaque-token"

    class Response:
        status = 202

        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    def open_request(request: Request, *, timeout: float) -> Response:
        requests.append((request, timeout))
        return Response()

    transport = MicrosoftGraphEmailTransport(
        Authenticator(),  # type: ignore[arg-type]
        opener=open_request,
    )
    transport.send(
        EmailEnvelope(
            "sender@hotmail.com",
            "recipient@example.com",
            "PAMS report",
            "Plain report",
            "<p>HTML report</p>",
        )
    )

    request, timeout = requests[0]
    assert request.full_url == "https://graph.microsoft.com/v1.0/me/sendMail"
    assert request.method == "POST"
    assert request.get_header("Authorization") == "Bearer opaque-token"
    assert request.get_header("Content-type") == "text/plain"
    assert timeout == 30
    message = BytesParser(policy=policy.default).parsebytes(
        base64.b64decode(request.data)
    )
    assert message["From"] == "sender@hotmail.com"
    assert message["To"] == "recipient@example.com"
    assert message.is_multipart()
    assert message.get_body(preferencelist=("plain",)).get_content().strip() == (
        "Plain report"
    )
    assert message.get_body(preferencelist=("html",)).get_content().strip() == (
        "<p>HTML report</p>"
    )


def test_graph_transport_embeds_inline_png_by_content_id() -> None:
    requests: list[Request] = []

    class Authenticator:
        def acquire_token_silent(self) -> str:
            return "opaque-token"

    class Response:
        status = 202

        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    def open_request(request: Request, *, timeout: float) -> Response:
        assert timeout == 30
        requests.append(request)
        return Response()

    MicrosoftGraphEmailTransport(
        Authenticator(),  # type: ignore[arg-type]
        opener=open_request,
    ).send(
        EmailEnvelope(
            "sender@hotmail.com",
            "recipient@example.com",
            "PAMS report",
            "Plain report",
            '<img src="cid:chart">',
            (InlineImage("chart", "chart.png", "image/png", b"png-content"),),
        )
    )

    message = BytesParser(policy=policy.default).parsebytes(
        base64.b64decode(requests[0].data)
    )
    attachment = next(
        part for part in message.walk() if part.get_content_type() == "image/png"
    )
    assert attachment["Content-ID"] == "<chart>"
    assert attachment.get_content_disposition() == "inline"
    assert attachment.get_filename() is None
    assert attachment.get_content_type() == "image/png"
    assert attachment.get_payload(decode=True) == b"png-content"


def test_graph_transport_rejects_non_accepted_response() -> None:
    class Authenticator:
        def acquire_token_silent(self) -> str:
            return "opaque-token"

    class Response:
        status = 403

        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    transport = MicrosoftGraphEmailTransport(
        Authenticator(),  # type: ignore[arg-type]
        opener=lambda *_args, **_kwargs: Response(),
    )

    with pytest.raises(MicrosoftGraphEmailError, match="HTTP 403"):
        transport.send(
            EmailEnvelope("from@example.com", "to@example.com", "s", "p", "<p>h</p>")
        )
