"""Public report-asset publishing through Supabase Storage."""

from collections.abc import Callable
from hashlib import sha256
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


class ReportAssetPublishError(RuntimeError):
    """A report asset could not be published safely."""


class SupabaseReportAssetStore:
    """Upsert generated report assets into one public Supabase bucket."""

    def __init__(
        self,
        supabase_url: str,
        service_role_key: str,
        bucket: str = "pams-report-assets",
        prefix: str | None = None,
        *,
        timeout_seconds: float = 30,
        opener: Callable[..., Any] = urlopen,
    ) -> None:
        base_url = supabase_url.strip().rstrip("/")
        if not base_url.startswith("https://"):
            raise ValueError("PAMS_SUPABASE_URL must be an HTTPS URL")
        if not service_role_key.strip():
            raise ValueError("PAMS_SUPABASE_SERVICE_ROLE_KEY is required")
        self._base_url = base_url
        self._service_role_key = service_role_key
        self._bucket = _safe_path(bucket, "bucket")
        generated_prefix = sha256(
            f"{base_url}\0{service_role_key}".encode()
        ).hexdigest()[:24]
        self._prefix = _safe_path(
            prefix.strip("/") if prefix else generated_prefix,
            "asset prefix",
        )
        self._timeout_seconds = timeout_seconds
        self._opener = opener

    def publish(
        self,
        content: bytes,
        content_type: str,
        object_name: str,
    ) -> str:
        """Upsert bytes at a deterministic path and return its public URL."""
        if not content:
            raise ValueError("report asset content must not be empty")
        if content_type != "image/png":
            raise ValueError("report asset content type must be image/png")
        safe_name = _safe_path(object_name, "object name")
        object_path = f"{self._prefix}/{safe_name}"
        encoded_bucket = quote(self._bucket, safe="")
        encoded_path = "/".join(quote(part, safe="") for part in object_path.split("/"))
        upload_url = (
            f"{self._base_url}/storage/v1/object/{encoded_bucket}/{encoded_path}"
        )
        request = Request(
            upload_url,
            data=content,
            headers={
                "Authorization": f"Bearer {self._service_role_key}",
                "apikey": self._service_role_key,
                "Content-Type": content_type,
                "x-upsert": "true",
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
                    raise ReportAssetPublishError(
                        f"Supabase Storage returned HTTP {status}"
                    )
        except HTTPError as error:
            raise ReportAssetPublishError(
                f"Supabase Storage returned HTTP {error.code}"
            ) from error
        except URLError as error:
            raise ReportAssetPublishError(
                f"Supabase Storage request failed: {type(error.reason).__name__}"
            ) from error
        return (
            f"{self._base_url}/storage/v1/object/public/"
            f"{encoded_bucket}/{encoded_path}"
        )


def _safe_path(value: str, label: str) -> str:
    parts = value.split("/")
    if not value or any(not part or part in {".", ".."} for part in parts):
        raise ValueError(f"Supabase report asset {label} is invalid")
    return "/".join(parts)
