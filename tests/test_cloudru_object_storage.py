from __future__ import annotations

import threading
import traceback
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit
from xml.sax.saxutils import escape

import pytest
import requests

from cloud.cloudru.object_storage import (
    CloudRuObjectStorage,
    S3Credentials,
    cloudru_s3_credentials,
    cloudru_storage_credential_resolver,
    sign_v4,
)
from runtime import RuntimeDispatcher, RuntimeStorage
from storage import (
    LocalDirectoryStorage,
    StorageCredentialsMissing,
    StorageError,
    StorageObjectNotFound,
    StorageProviderUnavailable,
    storage_provider_from_env,
)

SECRET = "super-secret-value"
CREDS = S3Credentials("tenant:key", SECRET)
FIXED_NOW = datetime(2026, 9, 28, 12, 0, 0, tzinfo=timezone.utc)


class FakeS3(ThreadingHTTPServer):
    """In-memory S3 subset: PUT/GET object and ListObjectsV2 with pagination."""

    def __init__(self, page_size=1000):
        super().__init__(("127.0.0.1", 0), FakeS3Handler)
        self.objects: dict[tuple[str, str], bytes] = {}
        self.requests: list[dict] = []
        self.page_size = page_size


class FakeS3Handler(BaseHTTPRequestHandler):
    server: FakeS3

    def log_message(self, *args):
        pass

    def _record(self):
        parts = urlsplit(self.path)
        segments = parts.path.lstrip("/").split("/", 1)
        bucket = unquote(segments[0])
        key = unquote(segments[1]) if len(segments) > 1 else ""
        auth = self.headers.get("Authorization", "")
        self.server.requests.append(
            {"method": self.command, "path": parts.path, "query": parts.query, "auth": auth}
        )
        return bucket, key, parse_qs(parts.query), auth

    def _send(self, status, body=b""):
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_PUT(self):
        bucket, key, _, auth = self._record()
        if "Credential=tenant:key/" not in auth:
            return self._send(403, b"<Error><Code>InvalidAccessKeyId</Code></Error>")
        length = int(self.headers.get("Content-Length", "0"))
        self.server.objects[(bucket, key)] = self.rfile.read(length)
        self._send(200)

    def do_GET(self):
        bucket, key, query, _ = self._record()
        if key:
            data = self.server.objects.get((bucket, key))
            return self._send(404) if data is None else self._send(200, data)
        prefix = query.get("prefix", [""])[0]
        start = int(query.get("continuation-token", ["0"])[0])
        keys = sorted(k for b, k in self.server.objects if b == bucket and k.startswith(prefix))
        page = keys[start : start + self.server.page_size]
        truncated = start + self.server.page_size < len(keys)
        body = ['<?xml version="1.0"?>']
        body.append('<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">')
        body.append(f"<IsTruncated>{'true' if truncated else 'false'}</IsTruncated>")
        if truncated:
            body.append(f"<NextContinuationToken>{start + len(page)}</NextContinuationToken>")
        for item in page:
            size = len(self.server.objects[(bucket, item)])
            body.append(f"<Contents><Key>{escape(item)}</Key><Size>{size}</Size></Contents>")
        body.append("</ListBucketResult>")
        self._send(200, "".join(body).encode())


@pytest.fixture
def fake_s3():
    server = FakeS3(page_size=2)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def adapter(server, **kwargs):
    host, port = server.server_address
    session = requests.Session()
    session.trust_env = False  # never route the local fake through an HTTP proxy
    return CloudRuObjectStorage(
        "alice-bucket", endpoint=f"http://{host}:{port}/", session=session, **kwargs
    )


def test_sigv4_matches_aws_documented_example():
    # "GET Object" example from the AWS Signature V4 for S3 documentation.
    headers = sign_v4(
        "GET",
        "https://examplebucket.s3.amazonaws.com/test.txt",
        {"Range": "bytes=0-9"},
        payload_sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        credentials=S3Credentials(
            "AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
        ),
        region="us-east-1",
        now=datetime(2013, 5, 24, tzinfo=timezone.utc),
    )

    assert headers["Authorization"] == (
        "AWS4-HMAC-SHA256 Credential=AKIAIOSFODNN7EXAMPLE/20130524/us-east-1/s3/aws4_request, "
        "SignedHeaders=host;range;x-amz-content-sha256;x-amz-date, "
        "Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"
    )


def test_upload_download_and_list_round_trip(fake_s3):
    storage = adapter(fake_s3, prefix="alice/files", clock=lambda: FIXED_NOW)

    saved = storage.upload("reports/итог 1.txt", b"result", credentials=CREDS)
    storage.upload("reports/b.txt", b"bb", credentials=CREDS)
    storage.upload("reports-old/c.txt", b"c", credentials=CREDS)
    storage.upload("other.txt", b"o", credentials=CREDS)

    assert saved.provider == "cloudru"
    assert saved.object_id == "reports/итог 1.txt"
    assert saved.name == "итог 1.txt"
    assert saved.size == 6
    assert saved.location == "s3://alice-bucket/alice/files/reports/итог 1.txt"
    assert ("alice-bucket", "alice/files/reports/итог 1.txt") in fake_s3.objects
    assert storage.download("reports/итог 1.txt", credentials=CREDS) == b"result"
    assert [item.object_id for item in storage.list("reports", credentials=CREDS)] == [
        "reports/b.txt",
        "reports/итог 1.txt",
    ]
    assert [item.object_id for item in storage.list("reports/b.txt", credentials=CREDS)] == [
        "reports/b.txt"
    ]
    everything = storage.list("", credentials=CREDS)
    assert [item.object_id for item in everything] == [
        "other.txt",
        "reports-old/c.txt",
        "reports/b.txt",
        "reports/итог 1.txt",
    ]
    assert [item.size for item in everything] == [1, 1, 2, 6]
    assert any("continuation-token" in request["query"] for request in fake_s3.requests)
    assert all(SECRET not in request["auth"] for request in fake_s3.requests)


def test_bucket_without_prefix_skips_directory_markers(fake_s3):
    storage = adapter(fake_s3)
    fake_s3.objects[("alice-bucket", "folder/")] = b""
    storage.upload(
        "folder/a.txt",
        b"a",
        credentials={"key_id": "key", "key_secret": "s", "tenant_id": "tenant"},
    )

    listed = storage.list(credentials=CREDS)

    assert [item.object_id for item in listed] == ["folder/a.txt"]
    assert listed[0].location == "s3://alice-bucket/folder/a.txt"


def test_missing_object_and_rejected_credentials(fake_s3):
    storage = adapter(fake_s3)

    with pytest.raises(StorageObjectNotFound):
        storage.download("missing.txt", credentials=CREDS)
    with pytest.raises(StorageCredentialsMissing) as excinfo:
        storage.upload("a.txt", b"a", credentials=S3Credentials("wrong", SECRET))
    assert "InvalidAccessKeyId" not in str(excinfo.value)
    with pytest.raises(StorageCredentialsMissing):
        storage.upload("a.txt", b"a", credentials=None)
    with pytest.raises(StorageCredentialsMissing):
        storage.upload("a.txt", b"a", credentials={"key_id": "only-id"})
    with pytest.raises(StorageError):
        storage.upload("../escape.txt", b"a", credentials=CREDS)


class FakeResponse:
    def __init__(self, status_code, content=b""):
        self.status_code = status_code
        self.content = content


class FakeSession:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.mark.parametrize(
    ("result", "error"),
    [
        (requests.ConnectionError("boom " + SECRET), StorageProviderUnavailable),
        (FakeResponse(503, b"internal " + SECRET.encode()), StorageProviderUnavailable),
        (FakeResponse(401), StorageCredentialsMissing),
        (FakeResponse(409, b"<Error>response-body-marker</Error>"), StorageError),
    ],
)
def test_api_errors_are_mapped_without_details(result, error):
    storage = CloudRuObjectStorage("bucket", session=FakeSession(result))

    with pytest.raises(error) as excinfo:
        storage.download("a.txt", credentials=CREDS)

    formatted = "".join(traceback.format_exception(excinfo.value))
    assert SECRET not in formatted
    assert "response-body-marker" not in formatted


def test_invalid_list_xml_is_a_safe_error():
    session = FakeSession(FakeResponse(200, b"not xml"))
    storage = CloudRuObjectStorage("bucket", session=session)

    with pytest.raises(StorageError) as excinfo:
        storage.list("", credentials=CREDS)
    assert excinfo.value.__cause__ is None
    method, url, kwargs = session.calls[0]
    assert method == "GET"
    assert url == "https://s3.cloud.ru/bucket?list-type=2&prefix="
    assert kwargs["timeout"] == 30.0
    assert "Credential=tenant:key/" in kwargs["headers"]["Authorization"]
    assert "/ru-central-1/s3/aws4_request" in kwargs["headers"]["Authorization"]


def test_malformed_list_size_is_a_safe_error():
    body = (
        b"<ListBucketResult><IsTruncated>false</IsTruncated>"
        b"<Contents><Key>a.txt</Key><Size>not-a-number</Size></Contents></ListBucketResult>"
    )
    storage = CloudRuObjectStorage("bucket", session=FakeSession(FakeResponse(200, body)))

    with pytest.raises(StorageError) as excinfo:
        storage.list("", credentials=CREDS)

    assert excinfo.value.__cause__ is None
    assert "not-a-number" not in str(excinfo.value)


def test_default_clock_and_session_are_used():
    storage = CloudRuObjectStorage("bucket")
    assert isinstance(storage._session, requests.Session)
    assert storage._clock().tzinfo is timezone.utc


@pytest.mark.parametrize("bucket", ["", "  ", "a/b"])
def test_bucket_is_required(bucket):
    with pytest.raises(StorageProviderUnavailable):
        CloudRuObjectStorage(bucket)


def test_credentials_repr_hides_secret():
    assert SECRET not in repr(CREDS)


def test_cloudru_s3_credentials():
    assert cloudru_s3_credentials("key", "secret", "tenant") == S3Credentials(
        "tenant:key", "secret"
    )
    assert cloudru_s3_credentials("tenant:key", "secret", "other") == S3Credentials(
        "tenant:key", "secret"
    )
    assert cloudru_s3_credentials("key", "secret") == S3Credentials("key", "secret")
    assert cloudru_s3_credentials("", "secret", "tenant") is None
    assert cloudru_s3_credentials("key", "", "tenant") is None


def test_credential_resolver_prefers_stored_key_then_env():
    env = {
        "CLOUDRU_STORAGE_TENANT_ID": "tenant",
        "CLOUDRU_IAM_KEY_ID": "env-key",
        "CLOUDRU_IAM_KEY_SECRET": "env-secret",
    }
    stored = cloudru_storage_credential_resolver(
        lambda: {"key_id": "db-key", "key_secret": "db-secret"}, env
    )
    incomplete = cloudru_storage_credential_resolver(lambda: {"key_id": "db-key"}, env)
    empty = cloudru_storage_credential_resolver(lambda: None, env)
    env_only = cloudru_storage_credential_resolver(env=env)

    assert stored(None, "cloudru") == S3Credentials("tenant:db-key", "db-secret")
    assert incomplete(None, "cloudru") == S3Credentials("tenant:env-key", "env-secret")
    assert empty(None, "cloudru") == S3Credentials("tenant:env-key", "env-secret")
    assert env_only(None, "cloudru") == S3Credentials("tenant:env-key", "env-secret")
    assert env_only(None, "local") is None
    assert cloudru_storage_credential_resolver(env={})(None, "cloudru") is None

    dedicated_env = {
        **env,
        "CLOUDRU_STORAGE_KEY_ID": "s3-key",
        "CLOUDRU_STORAGE_KEY_SECRET": "s3-secret",
    }
    dedicated = cloudru_storage_credential_resolver(
        lambda: {"key_id": "db-key", "key_secret": "db-secret"}, dedicated_env
    )
    assert dedicated(None, "cloudru") == S3Credentials("tenant:s3-key", "s3-secret")


def test_credential_resolver_reads_process_env(monkeypatch):
    monkeypatch.setenv("CLOUDRU_IAM_KEY_ID", "proc-key")
    monkeypatch.setenv("CLOUDRU_IAM_KEY_SECRET", "proc-secret")
    monkeypatch.delenv("CLOUDRU_STORAGE_TENANT_ID", raising=False)

    assert cloudru_storage_credential_resolver()(None, "cloudru") == S3Credentials(
        "proc-key", "proc-secret"
    )


def test_dispatcher_uses_cloudru_adapter_with_resolved_credentials(fake_s3):
    dispatcher = RuntimeDispatcher()
    dispatcher.register_runtime("runtime-a", owner_id="owner-a")
    dispatcher.register_storage_provider("runtime-a", adapter(fake_s3))
    storage = RuntimeStorage(dispatcher, "runtime-a")

    with pytest.raises(StorageCredentialsMissing):
        storage.list("cloudru")

    dispatcher.set_storage_credential_resolver(
        cloudru_storage_credential_resolver(
            env={
                "CLOUDRU_STORAGE_TENANT_ID": "tenant",
                "CLOUDRU_IAM_KEY_ID": "key",
                "CLOUDRU_IAM_KEY_SECRET": SECRET,
            }
        )
    )
    storage.upload("cloudru", "out/report.txt", b"report")
    assert storage.download("cloudru", "out/report.txt") == b"report"


def test_storage_provider_from_env(tmp_path, monkeypatch):
    local = storage_provider_from_env({"ALICE_STORAGE_LOCAL_ROOT": str(tmp_path / "a")})
    assert isinstance(local, LocalDirectoryStorage)
    assert (tmp_path / "a").is_dir()

    fallback = storage_provider_from_env({}, local_root=tmp_path / "b")
    assert isinstance(fallback, LocalDirectoryStorage)
    assert (tmp_path / "b").is_dir()

    cloud = storage_provider_from_env(
        {
            "ALICE_STORAGE_PROVIDER": " CloudRU ",
            "CLOUDRU_STORAGE_BUCKET": "alice",
            "CLOUDRU_STORAGE_ENDPOINT": "https://s3.example.test/",
            "CLOUDRU_STORAGE_REGION": "ru-test-1",
            "CLOUDRU_STORAGE_PREFIX": "prod",
        }
    )
    assert isinstance(cloud, CloudRuObjectStorage)
    assert cloud._endpoint == "https://s3.example.test"
    assert cloud._region == "ru-test-1"
    assert cloud._prefix == "prod"

    with pytest.raises(StorageProviderUnavailable):
        storage_provider_from_env({"ALICE_STORAGE_PROVIDER": "cloudru"})
    with pytest.raises(StorageProviderUnavailable):
        storage_provider_from_env({"ALICE_STORAGE_PROVIDER": "gdrive"})

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ALICE_STORAGE_PROVIDER", raising=False)
    monkeypatch.delenv("ALICE_STORAGE_LOCAL_ROOT", raising=False)
    monkeypatch.setenv("CLOUDRU_STORAGE_BUCKET", "from-process")
    assert isinstance(storage_provider_from_env(), LocalDirectoryStorage)
    assert (tmp_path / "data" / "storage").is_dir()
    assert CloudRuObjectStorage.from_env()._bucket == "from-process"
