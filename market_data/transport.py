"""HTTP transport boundary for official market-data APIs."""

import errno
import json
import logging
import random
import socket
import time
from collections.abc import Callable, Mapping, Sequence
from http.client import IncompleteRead
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from market_data.exceptions import ProviderDataError

JSONRecord = Mapping[str, object]
TRANSIENT_HTTP_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
MAX_HTTP_ATTEMPTS = 4
_TRANSIENT_ERRNOS = frozenset(
    {
        errno.ECONNABORTED,
        errno.ECONNREFUSED,
        errno.ECONNRESET,
        errno.EHOSTUNREACH,
        errno.ENETUNREACH,
        errno.ETIMEDOUT,
    }
)
_LOGGER = logging.getLogger(__name__)


class JSONTransport(Protocol):
    """Load a JSON array of records from an HTTP endpoint."""

    def get_records(self, url: str) -> Sequence[JSONRecord]: ...


class JSONDocumentTransport(Protocol):
    """Load a JSON object from an HTTP endpoint."""

    def get_document(self, url: str) -> JSONRecord: ...


def _is_transient_url_error(error: URLError) -> bool:
    reason = error.reason
    if isinstance(
        reason,
        (
            IncompleteRead,
            TimeoutError,
            socket.timeout,
            ConnectionResetError,
            ConnectionAbortedError,
        ),
    ):
        return True
    if isinstance(reason, socket.gaierror):
        return reason.errno == socket.EAI_AGAIN
    return isinstance(reason, OSError) and reason.errno in _TRANSIENT_ERRNOS


def _is_transient_transport_error(error: BaseException) -> bool:
    if isinstance(error, HTTPError):
        return error.code in TRANSIENT_HTTP_STATUS_CODES
    if isinstance(error, URLError):
        return _is_transient_url_error(error)
    return isinstance(
        error,
        (
            IncompleteRead,
            TimeoutError,
            socket.timeout,
            ConnectionResetError,
            ConnectionAbortedError,
        ),
    )


class _UrllibJSONTransportBase:
    """Read complete official HTTP responses with bounded transient retries."""

    def __init__(
        self,
        timeout_seconds: float = 15.0,
        attempts: int = MAX_HTTP_ATTEMPTS,
        *,
        provider_name: str = "official market data",
        opener: Callable[..., Any] = urlopen,
        sleeper: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = lambda: random.uniform(0, 0.25),
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if not 1 <= attempts <= MAX_HTTP_ATTEMPTS:
            raise ValueError(f"attempts must be between 1 and {MAX_HTTP_ATTEMPTS}")
        self.timeout_seconds = timeout_seconds
        self.attempts = attempts
        self.provider_name = provider_name
        self._opener = opener
        self._sleeper = sleeper
        self._jitter = jitter

    def _get_payload(self, url: str, user_agent: str) -> object:
        body = self._read_complete_body(url, user_agent)
        try:
            decoded = body.decode("utf-8-sig")
            return json.loads(decoded)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ProviderDataError(
                f"{self.provider_name} returned invalid JSON"
            ) from error

    def _read_complete_body(self, url: str, user_agent: str) -> bytes:
        request = Request(url, headers={"User-Agent": user_agent})
        endpoint_host = urlsplit(url).hostname or "official endpoint"
        for attempt in range(1, self.attempts + 1):
            try:
                with self._opener(
                    request, timeout=self.timeout_seconds
                ) as response:  # noqa: S310
                    return response.read()
            except Exception as error:
                if not _is_transient_transport_error(error):
                    raise ProviderDataError(
                        f"{self.provider_name} HTTP request failed: "
                        f"{type(error).__name__}"
                    ) from error
                _LOGGER.warning(
                    "Transient market-data transport failure: "
                    "provider=%s endpoint_host=%s attempt=%d/%d error=%s",
                    self.provider_name,
                    endpoint_host,
                    attempt,
                    self.attempts,
                    type(error).__name__,
                )
                if attempt == self.attempts:
                    raise ProviderDataError(
                        f"{self.provider_name} HTTP request failed after "
                        f"{self.attempts} attempts: {type(error).__name__}"
                    ) from error
                self._sleeper((2 ** (attempt - 1)) + self._jitter())
        raise AssertionError("unreachable")


class UrllibJSONTransport(_UrllibJSONTransportBase):
    """Standard-library array JSON transport with bounded retries."""

    def get_records(self, url: str) -> Sequence[JSONRecord]:
        """Fetch and validate an array-shaped JSON response."""
        payload = self._get_payload(url, "PAMS/1.0")
        if not isinstance(payload, list) or not all(
            isinstance(record, dict) for record in payload
        ):
            raise ProviderDataError("Market-data response must be an array of objects")
        return payload


class UrllibJSONDocumentTransport(_UrllibJSONTransportBase):
    """Standard-library object JSON transport with bounded retries."""

    def get_document(self, url: str) -> JSONRecord:
        """Fetch and validate an object-shaped JSON response."""
        payload = self._get_payload(url, "PAMS/1.0")
        if not isinstance(payload, dict):
            raise ProviderDataError("Market-data response must be an object")
        return payload
