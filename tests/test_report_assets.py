"""Offline tests for public report asset storage."""

from urllib.error import HTTPError
from urllib.request import Request

import pytest

from pams.delivery import ReportAssetPublishError, SupabaseReportAssetStore


def test_supabase_store_upserts_png_and_returns_public_https_url() -> None:
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

    store = SupabaseReportAssetStore(
        "https://project.supabase.co",
        "service-role-secret",
        "pams-report-assets",
        "deployment-random-prefix",
        opener=open_request,
    )
    result = store.publish(
        b"\x89PNG\r\n\x1a\npng-content",
        "image/png",
        "daily-report/2026-07-27/asset-change.png",
    )

    request, timeout = requests[0]
    expected_path = (
        "pams-report-assets/deployment-random-prefix/"
        "daily-report/2026-07-27/asset-change.png"
    )
    assert request.full_url == (
        f"https://project.supabase.co/storage/v1/object/{expected_path}"
    )
    assert request.method == "POST"
    assert request.data == b"\x89PNG\r\n\x1a\npng-content"
    assert request.get_header("Content-type") == "image/png"
    assert request.get_header("X-upsert") == "true"
    assert request.get_header("Authorization") == "Bearer service-role-secret"
    assert request.get_header("Apikey") == "service-role-secret"
    assert timeout == 30
    assert result == (
        "https://project.supabase.co/storage/v1/object/public/" + expected_path
    )


def test_supabase_store_derives_stable_unlisted_prefix_when_not_configured() -> None:
    urls: list[str] = []

    class Response:
        status = 200

        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    def open_request(request: Request, **_kwargs: object) -> Response:
        urls.append(request.full_url)
        return Response()

    for _ in range(2):
        SupabaseReportAssetStore(
            "https://project.supabase.co",
            "deployment-specific-secret",
            opener=open_request,
        ).publish(b"png", "image/png", "daily-report/date/chart.png")

    assert urls[0] == urls[1]
    assert "deployment-specific-secret" not in urls[0]
    assert "/pams-report-assets/daily-report/" not in urls[0]


def test_supabase_store_failure_is_typed_and_does_not_expose_key() -> None:
    def fail(*_args: object, **_kwargs: object) -> None:
        raise HTTPError(
            "https://project.supabase.co/storage/v1/object/bucket/object",
            403,
            "Forbidden",
            {},
            None,
        )

    secret = "service-role-must-not-leak"
    store = SupabaseReportAssetStore(
        "https://project.supabase.co",
        secret,
        prefix="random-prefix",
        opener=fail,
    )
    with pytest.raises(ReportAssetPublishError, match="HTTP 403") as raised:
        store.publish(b"png", "image/png", "daily-report/date/chart.png")
    assert secret not in str(raised.value)
