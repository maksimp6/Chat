"""Cloud.ru Evolution Object Storage adapter for the ``StorageProvider`` contract.

Object Storage speaks the S3 API. Requests are signed with AWS Signature V4 using
``requests`` so no provider SDK is needed. Credentials never reach logs, traces or
client-visible errors: failures are mapped to the safe errors from ``storage``.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any, Callable, Mapping
from urllib.parse import quote, urlsplit

import requests

from storage import (
    StorageCredentialsMissing,
    StorageError,
    StorageObject,
    StorageObjectNotFound,
    StorageProviderUnavailable,
    _safe_relative_path,
)

DEFAULT_ENDPOINT = "https://s3.cloud.ru"
DEFAULT_REGION = "ru-central-1"
_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
_CREDENTIAL_ERRORS = {401, 403}


@dataclass(frozen=True)
class S3Credentials:
    """S3 access key pair. ``repr`` never shows the secret."""

    access_key_id: str
    secret_access_key: str = field(repr=False)


def cloudru_s3_credentials(
    key_id: str, key_secret: str, tenant_id: str | None = None
) -> S3Credentials | None:
    """Build S3 credentials from a Cloud.ru service account access key.

    Cloud.ru expects the S3 access key ID as ``<tenant_id>:<key_id>``.
    """
    key_id = str(key_id or "").strip()
    key_secret = str(key_secret or "")
    tenant_id = str(tenant_id or "").strip()
    if not key_id or not key_secret:
        return None
    if tenant_id and ":" not in key_id:
        key_id = f"{tenant_id}:{key_id}"
    return S3Credentials(key_id, key_secret)


def _coerce_credentials(credentials: object | None) -> S3Credentials:
    if isinstance(credentials, S3Credentials):
        return credentials
    if isinstance(credentials, Mapping):
        resolved = cloudru_s3_credentials(
            credentials.get("access_key_id") or credentials.get("key_id") or "",
            credentials.get("secret_access_key") or credentials.get("key_secret") or "",
            credentials.get("tenant_id"),
        )
        if resolved is not None:
            return resolved
    raise StorageCredentialsMissing("Storage provider credentials are not configured")


def _hmac(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def _uri_encode(value: str, *, safe: str = "-_.~") -> str:
    return quote(value, safe=safe)


def sign_v4(
    method: str,
    url: str,
    headers: Mapping[str, str],
    *,
    payload_sha256: str,
    credentials: S3Credentials,
    region: str,
    now: datetime,
    service: str = "s3",
) -> dict[str, str]:
    """Return ``headers`` plus the SigV4 ``Authorization`` and date headers."""
    parts = urlsplit(url)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date = amz_date[:8]
    signed = {key.lower(): str(value).strip() for key, value in headers.items()}
    signed["host"] = parts.netloc
    signed["x-amz-date"] = amz_date
    signed["x-amz-content-sha256"] = payload_sha256
    names = sorted(signed)
    query_pairs = []
    for item in parts.query.split("&") if parts.query else []:
        key, _, value = item.partition("=")
        query_pairs.append((key, value))
    canonical_query = "&".join(f"{key}={value}" for key, value in sorted(query_pairs))
    canonical_request = "\n".join(
        [
            method,
            parts.path or "/",
            canonical_query,
            "".join(f"{name}:{signed[name]}\n" for name in names),
            ";".join(names),
            payload_sha256,
        ]
    )
    scope = f"{date}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )
    key = _hmac(("AWS4" + credentials.secret_access_key).encode("utf-8"), date)
    for part in (region, service, "aws4_request"):
        key = _hmac(key, part)
    signature = hmac.new(key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    result = dict(headers)
    result["x-amz-date"] = amz_date
    result["x-amz-content-sha256"] = payload_sha256
    result["Authorization"] = (
        f"AWS4-HMAC-SHA256 Credential={credentials.access_key_id}/{scope}, "
        f"SignedHeaders={';'.join(names)}, Signature={signature}"
    )
    return result


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child_text(element: ET.Element, name: str) -> str:
    for child in element:
        if _local_name(child.tag) == name:
            return child.text or ""
    return ""


class CloudRuObjectStorage:
    """S3-compatible adapter for a single Cloud.ru Object Storage bucket."""

    name = "cloudru"
    requires_credentials = True

    def __init__(
        self,
        bucket: str,
        *,
        endpoint: str = DEFAULT_ENDPOINT,
        region: str = DEFAULT_REGION,
        prefix: str = "",
        session: Any | None = None,
        timeout: float = 30.0,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        bucket = str(bucket or "").strip()
        if not bucket or "/" in bucket:
            raise StorageProviderUnavailable("Storage provider is not configured")
        self._bucket = bucket
        self._endpoint = (str(endpoint or "").strip() or DEFAULT_ENDPOINT).rstrip("/")
        self._region = str(region or "").strip() or DEFAULT_REGION
        base = _safe_relative_path(prefix, allow_empty=True)
        self._prefix = "" if base == PurePosixPath(".") else base.as_posix()
        self._session = session or requests.Session()
        self._timeout = timeout
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    @classmethod
    def from_env(
        cls, env: Mapping[str, str] | None = None, **kwargs: Any
    ) -> "CloudRuObjectStorage":
        env = os.environ if env is None else env
        return cls(
            env.get("CLOUDRU_STORAGE_BUCKET", ""),
            endpoint=env.get("CLOUDRU_STORAGE_ENDPOINT", ""),
            region=env.get("CLOUDRU_STORAGE_REGION", ""),
            prefix=env.get("CLOUDRU_STORAGE_PREFIX", ""),
            **kwargs,
        )

    def _key(self, object_id: str) -> tuple[PurePosixPath, str]:
        relative = _safe_relative_path(object_id)
        key = relative.as_posix()
        return relative, f"{self._prefix}/{key}" if self._prefix else key

    def _request(
        self,
        method: str,
        *,
        key: str = "",
        query: Mapping[str, str] | None = None,
        body: bytes = b"",
        credentials: object | None,
    ) -> Any:
        resolved = _coerce_credentials(credentials)
        path = f"/{_uri_encode(self._bucket)}"
        if key:
            path += "/" + _uri_encode(key, safe="-_.~/")
        url = self._endpoint + path
        if query:
            url += "?" + "&".join(
                f"{_uri_encode(name)}={_uri_encode(value)}" for name, value in sorted(query.items())
            )
        headers = sign_v4(
            method,
            url,
            {"Content-Length": str(len(body))} if method == "PUT" else {},
            payload_sha256=hashlib.sha256(body).hexdigest() if body else _EMPTY_SHA256,
            credentials=resolved,
            region=self._region,
            now=self._clock(),
        )
        try:
            response = self._session.request(
                method, url, data=body or None, headers=headers, timeout=self._timeout
            )
        except requests.RequestException as exc:
            raise StorageProviderUnavailable("Storage provider is unavailable") from exc
        status = int(response.status_code)
        if status == 404:
            raise StorageObjectNotFound("Storage object was not found")
        if status in _CREDENTIAL_ERRORS:
            raise StorageCredentialsMissing("Storage provider credentials were rejected")
        if status >= 500:
            raise StorageProviderUnavailable("Storage provider is unavailable")
        if status >= 300:
            raise StorageError("Storage provider operation failed")
        return response

    def _object(self, relative: PurePosixPath, key: str, size: int) -> StorageObject:
        return StorageObject(
            provider=self.name,
            object_id=relative.as_posix(),
            name=relative.name,
            size=size,
            location=f"s3://{self._bucket}/{key}",
        )

    def upload(
        self, object_id: str, content: bytes, *, credentials: object | None
    ) -> StorageObject:
        relative, key = self._key(object_id)
        data = bytes(content)
        self._request("PUT", key=key, body=data, credentials=credentials)
        return self._object(relative, key, len(data))

    def download(self, object_id: str, *, credentials: object | None) -> bytes:
        _, key = self._key(object_id)
        return bytes(self._request("GET", key=key, credentials=credentials).content)

    def list(self, prefix: str = "", *, credentials: object | None) -> list[StorageObject]:
        relative = _safe_relative_path(prefix, allow_empty=True)
        wanted = "" if relative == PurePosixPath(".") else relative.as_posix()
        root = f"{self._prefix}/" if self._prefix else ""
        search = root + wanted
        objects: list[StorageObject] = []
        token = ""
        while True:
            query = {"list-type": "2", "prefix": search}
            if token:
                query["continuation-token"] = token
            response = self._request("GET", query=query, credentials=credentials)
            try:
                tree = ET.fromstring(response.content)
            except ET.ParseError as exc:
                raise StorageError("Storage provider operation failed") from exc
            for item in tree:
                if _local_name(item.tag) != "Contents":
                    continue
                key = _child_text(item, "Key")
                object_id = key[len(root) :]
                if wanted and object_id != wanted and not object_id.startswith(wanted + "/"):
                    continue
                if not object_id or object_id.endswith("/"):
                    continue
                size = int(_child_text(item, "Size") or 0)
                objects.append(self._object(PurePosixPath(object_id), key, size))
            token = _child_text(tree, "NextContinuationToken")
            if _child_text(tree, "IsTruncated").lower() != "true" or not token:
                break
        return sorted(objects, key=lambda item: item.object_id)


def cloudru_storage_credential_resolver(
    load_iam_credentials: Callable[[], Mapping[str, str] | None] | None = None,
    env: Mapping[str, str] | None = None,
) -> Callable[[object, str], S3Credentials | None]:
    """Dispatcher resolver for Cloud.ru storage credentials.

    Uses the stored Cloud.ru IAM access key (``provider_credentials``) when a loader is
    given, then falls back to ``CLOUDRU_IAM_KEY_ID``/``CLOUDRU_IAM_KEY_SECRET``. The
    tenant prefix comes from ``CLOUDRU_STORAGE_TENANT_ID``.
    """

    def resolve(_context: object, provider: str) -> S3Credentials | None:
        if provider != CloudRuObjectStorage.name:
            return None
        values = os.environ if env is None else env
        tenant_id = values.get("CLOUDRU_STORAGE_TENANT_ID", "")
        stored = load_iam_credentials() if load_iam_credentials else None
        if stored:
            resolved = cloudru_s3_credentials(
                stored.get("key_id", ""), stored.get("key_secret", ""), tenant_id
            )
            if resolved is not None:
                return resolved
        return cloudru_s3_credentials(
            values.get("CLOUDRU_IAM_KEY_ID", ""),
            values.get("CLOUDRU_IAM_KEY_SECRET", ""),
            tenant_id,
        )

    return resolve
