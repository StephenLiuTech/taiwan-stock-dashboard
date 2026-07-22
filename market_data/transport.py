"""HTTP transport boundary for official market-data APIs."""

import json
from collections.abc import Mapping, Sequence
from typing import Protocol
from urllib.request import Request, urlopen

JSONRecord = Mapping[str, object]


class JSONTransport(Protocol):
    """Load a JSON array of records from an HTTP endpoint."""

    def get_records(self, url: str) -> Sequence[JSONRecord]: ...


class UrllibJSONTransport:
    """Small standard-library JSON transport with explicit timeout."""

    def __init__(self, timeout_seconds: float = 15.0) -> None:
        self.timeout_seconds = timeout_seconds

    def get_records(self, url: str) -> Sequence[JSONRecord]:
        """Fetch and validate an array-shaped JSON response."""
        request = Request(url, headers={"User-Agent": "PAMS/0.3"})
        with urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310
            payload = json.load(response)
        if not isinstance(payload, list) or not all(
            isinstance(record, dict) for record in payload
        ):
            raise ValueError("Market-data response must be an array of objects")
        return payload
