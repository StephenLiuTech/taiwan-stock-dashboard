"""Offline tests for the Resend email transport."""

import json
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from pams.application.send_daily_report import EmailEnvelope, InlineImage
from pams.delivery import ResendEmailError, ResendEmailTransport


def envelope() -> EmailEnvelope:
    return EmailEnvelope(
        "PAMS <reports@example.com>",
        "recipient@example.com",
        "PAMS daily report",
        "Plain report",
        "<p>HTML report</p>",
    )


def test_resend_transport_posts_text_and_html_without_printing_key(
    capsys: pytest.CaptureFixture[str],
) -> None:
    requests: list[tuple[Request, float]] = []

    class Response:
        status = 200

        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    def open_request(request: Request, *, timeout: float) -> Response:
        requests.append((request, timeout))
        return Response()

    api_key = "re_must-never-be-printed"
    ResendEmailTransport(api_key, opener=open_request).send(envelope())

    request, timeout = requests[0]
    assert request.full_url == "https://api.resend.com/emails"
    assert request.method == "POST"
    assert request.get_header("Authorization") == f"Bearer {api_key}"
    assert request.get_header("Content-type") == "application/json"
    assert request.get_header("User-agent") == "PAMS/1.0"
    assert timeout == 30
    assert json.loads(request.data) == {
        "from": "PAMS <reports@example.com>",
        "to": ["recipient@example.com"],
        "subject": "PAMS daily report",
        "text": "Plain report",
        "html": "<p>HTML report</p>",
    }
    captured = capsys.readouterr()
    assert api_key not in captured.out
    assert api_key not in captured.err


def test_resend_transport_rejects_non_success_response() -> None:
    class Response:
        status = 500

        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    transport = ResendEmailTransport(
        "re_secret", opener=lambda *_args, **_kwargs: Response()
    )

    with pytest.raises(ResendEmailError, match="HTTP 500"):
        transport.send(envelope())


def test_resend_transport_sends_inline_image_as_cid_attachment() -> None:
    requests: list[Request] = []

    class Response:
        status = 200

        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    def open_request(request: Request, *, timeout: float) -> Response:
        assert timeout == 30
        requests.append(request)
        return Response()

    message = envelope()
    ResendEmailTransport("re_secret", opener=open_request).send(
        EmailEnvelope(
            message.sender,
            message.recipient,
            message.subject,
            message.plain_text,
            '<img src="cid:chart">',
            (InlineImage("chart", "chart.png", "image/png", b"png-content"),),
        )
    )

    assert json.loads(requests[0].data)["attachments"] == [
        {
            "content": "cG5nLWNvbnRlbnQ=",
            "filename": "chart.png",
            "contentId": "chart",
        }
    ]


def test_resend_transport_translates_http_error_without_exposing_key() -> None:
    def fail(*_args: object, **_kwargs: object) -> None:
        raise HTTPError(
            "https://api.resend.com/emails",
            403,
            "Forbidden",
            {},
            None,
        )

    api_key = "re_private"
    transport = ResendEmailTransport(api_key, opener=fail)

    with pytest.raises(ResendEmailError, match="HTTP 403") as raised:
        transport.send(envelope())
    assert isinstance(raised.value.__cause__, HTTPError)
    assert api_key not in str(raised.value)
