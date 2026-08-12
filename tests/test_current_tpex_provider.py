"""Offline contracts for official current-day TPEx closing data."""

from collections.abc import Sequence
from datetime import date

import pytest

from market_data import (
    CurrentTPExProvider,
    DateAwareTPExProvider,
    MarketDateUnavailableError,
    ProviderDataError,
    SourceDateError,
    SourceDateMismatchError,
    TemporaryProviderUnavailableError,
)
from market_data.transport import JSONRecord


def current_records(source_date: str = "1150812") -> list[JSONRecord]:
    return [
        {
            "Date": source_date,
            "SecuritiesCompanyCode": "3293",
            "CompanyName": "IGS",
            "Close": "815.00",
            "Change": "+11.00",
        },
        {
            "Date": source_date,
            "SecuritiesCompanyCode": "8299",
            "CompanyName": "Phison",
            "Close": "2,120.00",
            "Change": "+30.00",
        },
    ]


class ArrayTransport:
    def __init__(self, value: Sequence[JSONRecord] | Exception) -> None:
        self.value = value
        self.url: str | None = None

    def get_records(self, url: str) -> Sequence[JSONRecord]:
        self.url = url
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


class Provider:
    def __init__(self, value: Sequence[JSONRecord] | Exception) -> None:
        self.value = value
        self.calls = 0

    def fetch(self) -> Sequence[JSONRecord]:
        self.calls += 1
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


def test_current_provider_accepts_complete_official_3293_and_8299_rows() -> None:
    transport = ArrayTransport(current_records())
    provider = CurrentTPExProvider(
        transport,
        expected_date=date(2026, 8, 12),
        required_symbols=frozenset({"3293", "8299"}),
    )

    records = provider.fetch()

    assert {record["SecuritiesCompanyCode"] for record in records} == {
        "3293",
        "8299",
    }
    assert transport.url == CurrentTPExProvider.endpoint


def test_current_provider_rejects_empty_publication() -> None:
    with pytest.raises(MarketDateUnavailableError, match="empty"):
        CurrentTPExProvider(ArrayTransport([])).fetch()


@pytest.mark.parametrize(
    "record",
    [
        {"Date": "1150812", "SecuritiesCompanyCode": "3293"},
        {"Date": "1150812", "CompanyName": "IGS", "Close": "815"},
    ],
)
def test_current_provider_rejects_malformed_rows(record: JSONRecord) -> None:
    with pytest.raises(ProviderDataError, match="required fields"):
        CurrentTPExProvider(ArrayTransport([record])).fetch()


def test_current_provider_rejects_source_date_mismatch_without_relabeling() -> None:
    with pytest.raises(SourceDateMismatchError) as captured:
        CurrentTPExProvider(
            ArrayTransport(current_records("1150811")),
            expected_date=date(2026, 8, 12),
        ).fetch()

    assert captured.value.actual == date(2026, 8, 11)


def test_current_provider_rejects_mixed_source_dates() -> None:
    records = current_records()
    records[1] = dict(records[1], Date="1150811")
    with pytest.raises(SourceDateError, match="mixed"):
        CurrentTPExProvider(ArrayTransport(records)).fetch()


def test_current_provider_rejects_missing_active_tpex_holding() -> None:
    with pytest.raises(ProviderDataError, match="8299"):
        CurrentTPExProvider(
            ArrayTransport(current_records()[:1]),
            required_symbols=frozenset({"3293", "8299"}),
        ).fetch()


def test_current_provider_preserves_typed_transient_failure() -> None:
    with pytest.raises(TemporaryProviderUnavailableError):
        CurrentTPExProvider(
            ArrayTransport(TemporaryProviderUnavailableError("HTTP 520"))
        ).fetch()


def test_today_uses_current_provider_when_historical_endpoint_is_empty() -> None:
    current = Provider(current_records())
    historical = Provider(MarketDateUnavailableError("empty historical date"))
    provider = DateAwareTPExProvider(
        date(2026, 8, 12),
        current,  # type: ignore[arg-type]
        historical,  # type: ignore[arg-type]
        today=lambda: date(2026, 8, 12),
    )

    assert provider.fetch() == current_records()
    assert current.calls == 1
    assert historical.calls == 0


def test_today_falls_back_only_to_exact_date_historical_provider() -> None:
    current = Provider(TemporaryProviderUnavailableError("HTTP 520"))
    historical = Provider(current_records())
    provider = DateAwareTPExProvider(
        date(2026, 8, 12),
        current,  # type: ignore[arg-type]
        historical,  # type: ignore[arg-type]
        today=lambda: date(2026, 8, 12),
    )

    assert provider.fetch() == current_records()
    assert current.calls == historical.calls == 1


def test_prior_date_bypasses_current_provider() -> None:
    current = Provider(AssertionError("current provider should not run"))
    historical = Provider(current_records("1150811"))
    provider = DateAwareTPExProvider(
        date(2026, 8, 11),
        current,  # type: ignore[arg-type]
        historical,  # type: ignore[arg-type]
        today=lambda: date(2026, 8, 12),
    )

    assert provider.fetch() == current_records("1150811")
    assert current.calls == 0
    assert historical.calls == 1


def test_future_date_is_never_accepted() -> None:
    provider = DateAwareTPExProvider(
        date(2026, 8, 13),
        Provider(current_records()),  # type: ignore[arg-type]
        Provider(current_records()),  # type: ignore[arg-type]
        today=lambda: date(2026, 8, 12),
    )

    with pytest.raises(MarketDateUnavailableError, match="future"):
        provider.fetch()
