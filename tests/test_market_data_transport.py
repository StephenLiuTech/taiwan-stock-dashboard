"""Regression tests for bounded official market-data HTTP retries."""

import json
import logging
import socket
from http.client import IncompleteRead
from urllib.error import HTTPError, URLError

import pytest

from market_data import ProviderDataError, TemporaryProviderUnavailableError
from market_data.transport import UrllibJSONDocumentTransport


class Response:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def __enter__(self) -> "Response":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.content


class SequenceOpener:
    def __init__(self, *outcomes: Response | Exception) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    def __call__(self, _request: object, *, timeout: float) -> Response:
        assert timeout == 3
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def document_response() -> Response:
    return Response(json.dumps({"date": "20260727"}).encode())


def transport(
    opener: SequenceOpener,
    *,
    attempts: int = 4,
    sleeps: list[float] | None = None,
) -> UrllibJSONDocumentTransport:
    delays = sleeps if sleeps is not None else []
    return UrllibJSONDocumentTransport(
        timeout_seconds=3,
        attempts=attempts,
        provider_name="TPEx",
        opener=opener,
        sleeper=delays.append,
        jitter=lambda: 0,
    )


def test_incomplete_read_is_retried_before_json_parsing() -> None:
    opener = SequenceOpener(
        IncompleteRead(b'{"date":'),
        document_response(),
    )
    delays: list[float] = []

    assert transport(opener, sleeps=delays).get_document(
        "https://example.test/data?secret=hidden"
    ) == {"date": "20260727"}
    assert opener.calls == 2
    assert delays == [1]


def test_repeated_incomplete_read_exhausts_four_attempts() -> None:
    opener = SequenceOpener(*(IncompleteRead(b"partial") for _ in range(4)))
    delays: list[float] = []

    with pytest.raises(ProviderDataError, match="failed after 4 attempts"):
        transport(opener, sleeps=delays).get_document("https://example.test/data")

    assert opener.calls == 4
    assert delays == [1, 2, 4]


def test_transient_http_503_is_retried() -> None:
    opener = SequenceOpener(
        HTTPError("https://example.test", 503, "unavailable", {}, None),
        document_response(),
    )

    assert transport(opener).get_document("https://example.test/data") == {
        "date": "20260727"
    }
    assert opener.calls == 2


def test_transient_http_520_is_retried_with_bounded_attempts() -> None:
    opener = SequenceOpener(
        HTTPError("https://example.test", 520, "temporary", {}, None),
        document_response(),
    )

    assert transport(opener).get_document("https://example.test/data") == {
        "date": "20260727"
    }
    assert opener.calls == 2


def test_http_520_exhaustion_is_typed_and_never_retries_indefinitely() -> None:
    opener = SequenceOpener(
        *(
            HTTPError("https://example.test", 520, "temporary", {}, None)
            for _ in range(4)
        )
    )

    with pytest.raises(TemporaryProviderUnavailableError, match="4 attempts"):
        transport(opener).get_document("https://example.test/data")

    assert opener.calls == 4


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError(),
        socket.timeout(),  # noqa: UP041 - explicit transport contract case
        ConnectionResetError(),
        ConnectionAbortedError(),
        URLError(TimeoutError()),
        URLError(ConnectionResetError()),
    ],
)
def test_transient_connection_errors_are_retried(error: Exception) -> None:
    opener = SequenceOpener(error, document_response())

    assert transport(opener).get_document("https://example.test/data") == {
        "date": "20260727"
    }
    assert opener.calls == 2


def test_http_403_is_not_retried() -> None:
    opener = SequenceOpener(
        HTTPError("https://example.test", 403, "forbidden", {}, None),
        document_response(),
    )

    with pytest.raises(ProviderDataError, match="HTTPError"):
        transport(opener).get_document("https://example.test/data")

    assert opener.calls == 1


def test_complete_malformed_json_is_not_retried() -> None:
    opener = SequenceOpener(Response(b'{"date":'), document_response())

    with pytest.raises(ProviderDataError, match="invalid JSON"):
        transport(opener).get_document("https://example.test/data")

    assert opener.calls == 1


def test_attempt_count_is_bounded() -> None:
    with pytest.raises(ValueError, match="between 1 and 4"):
        UrllibJSONDocumentTransport(attempts=5)


def test_retry_log_excludes_query_and_exception_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    opener = SequenceOpener(
        URLError(TimeoutError("access_token=secret")),
        document_response(),
    )
    caplog.set_level(logging.WARNING, logger="market_data.transport")

    transport(opener).get_document("https://example.test/data?credential=secret")

    assert "provider=TPEx" in caplog.text
    assert "attempt=1/4" in caplog.text
    assert "error=URLError" in caplog.text
    assert "credential" not in caplog.text
    assert "access_token" not in caplog.text
