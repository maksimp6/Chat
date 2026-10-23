"""Read GitHub evidence into the repository RAG archive without running Logos.

Inventories precede expensive per-object reads. Limits produce explicit gaps;
they never turn a partial archive into a complete one. The cache is local,
credential-scoped, hash-checked, and only reuses mutable responses after a 304.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit


API = "https://api.github.com"
MAX_PAGE_BYTES = 32 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 128 * 1024 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
MAX_REQUESTS = 1500
MAX_PAGES = 10000
DOWNLOAD_TIMEOUT_SECONDS = 45
RETRYABLE_ERRORS = {"github_json_invalid", "github_http_502", "github_http_503", "github_http_504"}


class CollectionError(ValueError):
    """Safe reason only: no transport error body, signed URL, or credential."""


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise CollectionError("download_deadline_exceeded")
    return remaining


def _public_attachment_url(url):
    parsed = urlsplit(url)
    return (
        parsed.scheme == "https"
        and parsed.netloc == "github.com"
        and not parsed.query
        and not parsed.fragment
        and re.fullmatch(
            r"/user-attachments/(?:files/[1-9][0-9]*/[^/]+|"
            r"assets/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
            parsed.path,
        )
        is not None
    )


class _DownloadRedirect(urllib.request.HTTPRedirectHandler):
    deadline = None

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if self.deadline is not None:
            req.timeout = min(req.timeout, _remaining(self.deadline))
        parsed = urlsplit(newurl)
        host = (parsed.hostname or "").lower()
        allowed = host in {
            "api.github.com",
            "github.com",
            "github-production-user-asset-6210df.s3.amazonaws.com",
        } or host.endswith((".githubusercontent.com", ".github.com", ".blob.core.windows.net"))
        if parsed.scheme != "https" or not allowed or parsed.username or parsed.password:
            raise CollectionError("download_redirect_not_allowed")
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None:
            redirected.remove_header("Authorization")
            redirected.remove_header("If-none-match")
        return redirected


class GitHubReader:
    """Bounded, resumable GETs and fixed read-only GraphQL operations."""

    def __init__(
        self,
        repository: str,
        token: str,
        cache_dir: Path,
        *,
        max_requests: int = MAX_REQUESTS,
        max_total_bytes: int = MAX_TOTAL_BYTES,
        max_download_bytes: int = MAX_DOWNLOAD_BYTES,
    ):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise ValueError("repository_name_invalid")
        if any(
            type(value) is not int or value < 1
            for value in (max_requests, max_total_bytes, max_download_bytes)
        ):
            raise ValueError("github_collection_limit_invalid")
        self.repository = repository
        self.token = token
        scope = hashlib.sha256((repository + "\0" + token).encode()).hexdigest()
        self.cache_dir = Path(cache_dir) / "github" / scope
        self.cache_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.max_requests = max_requests
        self.max_total_bytes = max_total_bytes
        self.max_download_bytes = max_download_bytes
        self.requests = 0
        self.downloaded_bytes = 0
        self.cache_hits = 0
        self.stop_reason: str | None = None
        self.redirect_handler = _DownloadRedirect()
        self.opener = urllib.request.build_opener(self.redirect_handler)

    def _cache_paths(self, key: str) -> tuple[Path, Path]:
        digest = hashlib.sha256(key.encode()).hexdigest()
        return self.cache_dir / (digest + ".json"), self.cache_dir / (digest + ".body")

    def _cached(self, key: str, limit: int) -> tuple[bytes, dict] | None:
        meta_path, body_path = self._cache_paths(key)
        try:
            if meta_path.is_symlink() or body_path.is_symlink():
                return None
            if meta_path.stat().st_size > 16384 or body_path.stat().st_size > limit:
                return None
            meta = json.loads(meta_path.read_bytes())
            with body_path.open("rb") as handle:
                raw = handle.read(limit + 1)
            if (
                len(raw) > limit
                or meta.get("key") != key
                or meta.get("sha256") != hashlib.sha256(raw).hexdigest()
                or meta.get("bytes") != len(raw)
                or not isinstance(meta.get("etag"), (str, type(None)))
            ):
                return None
            return raw, meta
        except (OSError, ValueError, TypeError, AttributeError):
            return None

    def _save_cache(self, key: str, raw: bytes, etag: str | None) -> None:
        meta_path, body_path = self._cache_paths(key)
        meta = {
            "key": key,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
            "etag": etag,
        }
        # This derived cache is not an evidence journal. Atomic replacement avoids
        # treating an interrupted write as a successful cached response.
        for path, content in ((body_path, raw), (meta_path, json.dumps(meta).encode())):
            temporary = path.with_suffix(path.suffix + ".tmp")
            with open(temporary, "wb") as handle:
                os.chmod(temporary, 0o600)
                handle.write(content)
            temporary.replace(path)

    def _discard_cache(self, key):
        for path in self._cache_paths(key):
            path.unlink(missing_ok=True)

    @staticmethod
    def _decode_json(raw):
        try:
            return json.loads(raw)
        except (ValueError, UnicodeError):
            raise CollectionError("github_json_invalid") from None

    def _json_cache(self, key, cached):
        if cached is not None:
            try:
                self._decode_json(cached[0])
            except CollectionError:
                self._discard_cache(key)
                return None
        return cached

    def _request(
        self,
        path: str,
        *,
        body: bytes | None = None,
        binary: bool = False,
        immutable: bool = False,
    ) -> bytes:
        for attempt in range(2):
            try:
                return self._request_once(path, body=body, binary=binary, immutable=immutable)
            except CollectionError as error:
                if str(error) not in RETRYABLE_ERRORS:
                    raise
                if str(error) == "github_json_invalid":
                    self._discard_cache(self._request_key(path, body, binary))
                if attempt:
                    raise

    def _request_once(self, path, *, body=None, binary=False, immutable=False):
        key = self._request_key(path, body, binary)
        limit = self.max_download_bytes if binary else MAX_PAGE_BYTES
        cached = self._cached(key, limit)
        if not binary:
            cached = self._json_cache(key, cached)
        if immutable and cached:
            self.cache_hits += 1
            return cached[0]
        self._check_budget()
        headers = self._request_headers(path, body, binary, cached)
        self.requests += 1
        request = urllib.request.Request(API + path, data=body, headers=headers)
        try:
            raw, etag = self._read_response(request, limit)
        except urllib.error.HTTPError as error:
            return self._http_error(error, cached)
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError):
            raise CollectionError("github_transport_unavailable") from None
        if not binary:
            self._decode_json(raw)
        self._save_cache(key, raw, etag)
        return raw

    @staticmethod
    def _request_key(path, body, binary):
        if not path.startswith("/") or path.startswith("//") or "\n" in path or "\r" in path:
            raise CollectionError("github_path_invalid")
        if body is not None and path != "/graphql":
            raise CollectionError("github_write_forbidden")
        key = path + ("|octet-stream" if binary else "|json")
        if body is not None:
            key += "|" + hashlib.sha256(body).hexdigest()
        return key

    def _check_budget(self):
        if self.stop_reason:
            raise CollectionError(self.stop_reason)
        if self.requests >= self.max_requests:
            self.stop_reason = "request_budget_exhausted"
            raise CollectionError(self.stop_reason)
        if self.downloaded_bytes >= self.max_total_bytes:
            self.stop_reason = "download_budget_exhausted"
            raise CollectionError(self.stop_reason)

    def _request_headers(self, path, body, binary, cached):
        release_asset = binary and re.fullmatch(
            r"/repos/[^/]+/[^/]+/releases/assets/[1-9][0-9]*", path
        )
        headers = {
            # Actions downloads negotiate a JSON API redirect. Only release
            # assets request octet-stream from the REST endpoint itself.
            "Accept": "application/octet-stream"
            if release_asset
            else "application/vnd.github+json",
            "User-Agent": "repository-corpus-collector",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        if body is not None:
            headers["Content-Type"] = "application/json"
        elif cached and cached[1].get("etag"):
            headers["If-None-Match"] = cached[1]["etag"]
        return headers

    def _read_response(self, request, limit):
        with self.opener.open(request, timeout=45) as response:
            raw = response.read(min(limit, self.max_total_bytes - self.downloaded_bytes) + 1)
            self.downloaded_bytes += len(raw)
            if len(raw) > limit:
                raise CollectionError("source_byte_limit_exceeded")
            if self.downloaded_bytes > self.max_total_bytes:
                self.stop_reason = "download_budget_exhausted"
                raise CollectionError(self.stop_reason)
            return raw, response.headers.get("ETag")

    def _http_error(self, error, cached):
        if error.code == 304 and cached:
            self.cache_hits += 1
            return cached[0]
        if error.code == 401:
            self.stop_reason = "github_http_401"
        elif error.code in {403, 429} and (
            error.code == 429 or error.headers.get("X-RateLimit-Remaining") == "0"
        ):
            self.stop_reason = "github_rate_limit"
        raise CollectionError(self.stop_reason or f"github_http_{error.code}") from None

    def _json_request(self, path, *, body=None, immutable=False):
        return self._decode_json(self._request(path, body=body, immutable=immutable))

    def json(self, path: str, *, immutable: bool = False):
        return self._json_request(path, immutable=immutable)

    def binary(self, path: str, *, immutable: bool = False) -> bytes:
        return self._request(path, binary=True, immutable=immutable)

    def attachment(self, url: str) -> bytes:
        deadline = time.monotonic() + DOWNLOAD_TIMEOUT_SECONDS
        for attempt in range(2):
            try:
                return self._attachment_once(url, deadline)
            except CollectionError as error:
                if str(error) not in RETRYABLE_ERRORS or attempt:
                    raise

    def _attachment_once(self, url, deadline):
        if not _public_attachment_url(url):
            raise CollectionError("public_attachment_url_not_allowed")
        key = url + "|octet-stream"
        cached = self._cached(key, self.max_download_bytes)
        if cached:
            self.cache_hits += 1
            return cached[0]
        self._check_budget()
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/octet-stream",
                "User-Agent": "repository-corpus-collector",
            },
        )
        self.requests += 1
        self.redirect_handler.deadline = deadline
        try:
            raw, etag = self._read_attachment(request, deadline)
        except urllib.error.HTTPError as error:
            return self._http_error(error, None)
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError):
            reason = (
                "download_deadline_exceeded"
                if time.monotonic() >= deadline
                else "github_transport_unavailable"
            )
            raise CollectionError(reason) from None
        finally:
            self.redirect_handler.deadline = None
        self._save_cache(key, raw, etag)
        return raw

    def _read_attachment(self, request, deadline):
        chunks, size = [], 0
        with self.opener.open(request, timeout=_remaining(deadline)) as response:
            while True:
                remaining = _remaining(deadline)
                sock = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
                if sock is not None:
                    sock.settimeout(remaining)
                allowance = min(
                    self.max_download_bytes - size, self.max_total_bytes - self.downloaded_bytes
                )
                chunk = getattr(response, "read1", response.read)(min(65536, allowance + 1))
                if not chunk:
                    break
                size += len(chunk)
                self.downloaded_bytes += len(chunk)
                if size > self.max_download_bytes:
                    raise CollectionError("source_byte_limit_exceeded")
                if self.downloaded_bytes > self.max_total_bytes:
                    self.stop_reason = "download_budget_exhausted"
                    raise CollectionError(self.stop_reason)
                chunks.append(chunk)
            _remaining(deadline)
            return b"".join(chunks), response.headers.get("ETag")

    def graphql(self, query: str, variables: dict) -> dict:
        if not query.lstrip().startswith("query ") or re.search(r"\bmutation\b", query):
            raise CollectionError("github_write_forbidden")
        result = self._json_request(
            "/graphql", body=json.dumps({"query": query, "variables": variables}).encode()
        )
        if not isinstance(result, dict) or result.get("errors"):
            raise CollectionError("github_graphql_unavailable")
        return result


def _encoded(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


class _Progress:
    """Scheduling cursor only; never evidence of archive or index completeness."""

    def __init__(self, directory: Path, repository: str):
        self.path = directory / "collection-progress.json"
        self.repository = repository
        self.families = {}
        try:
            if self.path.is_symlink() or self.path.stat().st_size > 32768:
                return
            saved = json.loads(self.path.read_bytes())
            data = saved["data"]
            if (
                data.get("repository") == repository
                and data.get("schema") == 1
                and isinstance(data.get("families"), dict)
                and saved.get("sha256") == hashlib.sha256(_encoded(data)).hexdigest()
            ):
                self.families = data["families"]
        except (OSError, ValueError, TypeError, AttributeError, KeyError):
            pass

    def rotate(self, family, rows):
        ids = [row["id"] for row in rows]
        digest = hashlib.sha256(_encoded(ids)).hexdigest()
        previous = self.families.get(family, {})
        last_id = previous.get("last_id") if isinstance(previous, dict) else None
        if last_id not in ids:
            return rows, set(), digest
        split = ids.index(last_id) + 1
        historical = set(ids[:split]) if previous.get("inventory_sha256") == digest else set()
        return rows[split:] + rows[:split], historical, digest

    def advance(self, family, source_id, digest):
        self.families[family] = {"last_id": source_id, "inventory_sha256": digest}
        data = {"schema": 1, "repository": self.repository, "families": self.families}
        content = _encoded({"data": data, "sha256": hashlib.sha256(_encoded(data)).hexdigest()})
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path.with_suffix(".tmp")
        with temporary.open("wb") as handle:
            os.chmod(temporary, 0o600)
            handle.write(content)
        temporary.replace(self.path)


class _Collector:
    def __init__(self, repository, reader, sink, max_pages, progress):
        self.repository = repository
        self.base = f"/repos/{repository}"
        self.web = f"https://github.com/{repository}"
        self.reader = reader
        self.sink = sink
        self.max_pages = max_pages
        self.counts: dict[str, int] = {}
        self.gaps = 0
        self.inventory: dict[str, dict] = {}
        self.attachments: dict[str, dict] = {}
        self.attachment_parents_recorded: dict[str, set] = {}
        self.attachment_failures: dict[str, str] = {}
        self.unsupported_attachments: set[str] = set()
        self.progress = progress
        self.prior_attempts: dict[str, set] = {}
        self.family_inventory: dict[str, str] = {}

    def gap(self, kind, source_id, reason, metadata=None):
        self.gaps += 1
        self.sink.gap(kind, source_id, reason, metadata or {})

    def record(self, kind, row, *, source_id=None, url=None):
        identity = source_id or str(row.get("id") or row.get("node_id") or row.get("number") or "")
        if not identity:
            self.gap(kind, kind, "source_identity_missing")
            return
        raw = _encoded(row)
        version = str(
            row.get("updated_at")
            or row.get("updatedAt")
            or row.get("submitted_at")
            or hashlib.sha256(raw).hexdigest()
        )
        self.sink.add(
            kind,
            f"github:{self.repository}:{kind}:{identity}",
            url or row.get("html_url") or row.get("url") or self.web,
            version,
            raw,
            {"repository": self.repository, "source_id": identity, "access_scope": "repository"},
        )
        self.counts[kind] = self.counts.get(kind, 0) + 1
        self._attachment_links(
            row.get("body"),
            {
                "source_id": f"github:{self.repository}:{kind}:{identity}",
                "kind": kind,
                "version": version,
                "content_sha256": hashlib.sha256(raw).hexdigest(),
            },
        )

    def _attachment_links(self, body, parent):
        if isinstance(body, str):
            for match in re.finditer(
                r"https://(?:github\.com/(?:user-attachments|[^/\s)]+/[^/\s)]+/files)/|(?:user-images|private-user-images)\.githubusercontent\.com/)[^\s)<>\"]+",
                body,
            ):
                parsed = urlsplit(match.group())
                reference = parsed.scheme + "://" + parsed.netloc + parsed.path
                if _public_attachment_url(reference):
                    parents = self.attachments.setdefault(reference, {})
                    parents[parent["source_id"]] = parent
                elif reference not in self.unsupported_attachments:
                    self.unsupported_attachments.add(reference)
                    self.gap(
                        "github_attachment",
                        reference,
                        "linked_attachment_content_not_collected",
                        {"parent_source_id": parent["source_id"]},
                    )

    def pages(self, kind, path, *, key=None, immutable=False):
        """Exhaust pages and reconcile declared counts; repeated pages are gaps."""
        result, identities = [], set()
        state = {"expected": None, "consistent": True}
        complete = False
        separator = "&" if "?" in path else "?"
        try:
            for page in range(1, self.max_pages + 1):
                response = self.reader.json(
                    f"{self.base}{path}{separator}per_page=100&page={page}", immutable=immutable
                )
                rows = self._collection_rows(kind, path, response, key, state)
                if not rows:
                    complete = True
                    break
                self._append_page_rows(kind, rows, result, identities, state)
                if len(rows) < 100:
                    complete = True
                    break
            if not complete:
                raise CollectionError("pagination_limit_exceeded")
            if state["expected"] is not None and state["expected"] != len(result):
                complete = False
                self.gap(
                    kind,
                    path,
                    "inventory_count_mismatch",
                    {"expected": state["expected"], "collected": len(result)},
                )
        except CollectionError as error:
            self.gap(
                kind, path, str(error), {"collected": len(result), "expected": state["expected"]}
            )
        self.inventory[kind + ":" + path] = {
            "complete": complete and state["consistent"],
            "count": len(result),
            "expected": state["expected"],
        }
        return result

    def _collection_rows(self, kind, path, response, key, state):
        if key:
            if not isinstance(response, dict) or not isinstance(response.get(key), list):
                raise CollectionError("collection_shape_invalid")
            rows = response[key]
            total = response.get("total_count")
            if type(total) is int:
                if state["expected"] is not None and total != state["expected"]:
                    state["consistent"] = False
                    self.gap(
                        kind,
                        path,
                        "inventory_changed_during_collection",
                        {"before": state["expected"], "after": total},
                    )
                state["expected"] = total
        else:
            rows = response
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise CollectionError("collection_shape_invalid")
        return rows

    def _append_page_rows(self, kind, rows, result, identities, state):
        additions = 0
        for row in rows:
            identity = row.get("id") or row.get("node_id") or row.get("number")
            if type(identity) not in {str, int} or not identity:
                raise CollectionError("source_identity_missing")
            if identity in identities:
                state["consistent"] = False
                self.gap(kind, str(identity), "duplicate_id_during_pagination")
                continue
            identities.add(identity)
            result.append(row)
            additions += 1
        if not additions:
            raise CollectionError("pagination_repeated_page")

    def read_object(self, kind, path, *, immutable=False):
        try:
            row = self.reader.json(self.base + path, immutable=immutable)
            if not isinstance(row, dict):
                raise CollectionError("source_shape_invalid")
            self.record(kind, row, source_id=path, url=API + self.base + path)
            return row
        except CollectionError as error:
            self.gap(kind, path, str(error))
            return None

    def download(self, kind, path, row, *, immutable=True):
        if row.get("expired"):
            self.gap(
                kind,
                path,
                "artifact_expired",
                {"id": row.get("id"), "expires_at": row.get("expires_at")},
            )
            return
        size = row.get("size_in_bytes", row.get("size"))
        limit = getattr(self.reader, "max_download_bytes", MAX_DOWNLOAD_BYTES)
        if type(size) is int and size > limit:
            self.gap(kind, path, "source_byte_limit_exceeded", {"bytes": size, "limit": limit})
            return
        try:
            raw = self.reader.binary(self.base + path, immutable=immutable)
            digest = hashlib.sha256(raw).hexdigest()
            expected = row.get("digest")
            if (
                isinstance(expected, str)
                and expected.startswith("sha256:")
                and expected[7:] != digest
            ):
                raise CollectionError("download_digest_mismatch")
            if kind in {"actions_logs", "actions_artifact"} and not raw.startswith(b"PK"):
                raise CollectionError("download_zip_invalid")
            self.sink.add(
                kind,
                f"github:{self.repository}:{path}",
                API + self.base + path,
                str(row.get("updated_at") or digest),
                raw,
                {
                    "repository": self.repository,
                    "id": row.get("id"),
                    "name": row.get("name"),
                    "content_type": "application/zip"
                    if kind in {"actions_logs", "actions_artifact"}
                    else row.get("content_type"),
                    "access_scope": "repository",
                },
            )
            self.counts[kind] = self.counts.get(kind, 0) + 1
        except CollectionError as error:
            self.gap(kind, path, str(error), {"id": row.get("id")})

    def deferred(self, kind, rows, *, reason=None):
        if rows:
            reason = (
                reason or getattr(self.reader, "stop_reason", None) or "request_budget_exhausted"
            )
            ids = [row["id"] for row in rows]
            metadata = {"count": len(rows), "source_ids": ids}
            historical = [
                source_id for source_id in ids if source_id in self.prior_attempts.get(kind, set())
            ]
            if historical:
                metadata.update(
                    {
                        "previously_attempted_source_ids": historical,
                        "prior_attempt_state": "historical_not_revalidated_this_pass",
                    }
                )
            self.gap(kind, kind + ":remaining", reason, metadata)

    def resume(self, family, rows):
        ordered, historical, digest = self.progress.rotate(family, rows)
        self.prior_attempts[family] = historical
        self.family_inventory[family] = digest
        return ordered

    def advance(self, family, row):
        self.progress.advance(family, row["id"], self.family_inventory[family])

    def phase_limit(self, fraction):
        maximum = getattr(self.reader, "max_requests", None)
        used = getattr(self.reader, "requests", None)
        if type(maximum) is not int or type(used) is not int:
            return None
        return used + max(1, int((maximum - used) * fraction))

    def phase_exhausted(self, limit):
        return limit is not None and self.reader.requests >= limit


_DISCUSSIONS = """query RepositoryDiscussions($owner:String!,$name:String!,$cursor:String) {
 repository(owner:$owner,name:$name) {
  discussions(first:100,after:$cursor,orderBy:{field:CREATED_AT,direction:ASC}) {
   totalCount pageInfo { hasNextPage endCursor }
   nodes { id number title body url createdAt updatedAt author { login }
    category { id name } answer { id } comments { totalCount } }
  }
 }
}"""
_COMMENTS = """query DiscussionComments($id:ID!,$cursor:String) {
 node(id:$id) { ... on Discussion {
  comments(first:100,after:$cursor) { totalCount pageInfo { hasNextPage endCursor }
   nodes { id body url createdAt updatedAt author { login } replies { totalCount } }
  }
 } }
}"""
_REPLIES = """query DiscussionReplies($id:ID!,$cursor:String) {
 node(id:$id) { ... on DiscussionComment {
  replies(first:100,after:$cursor) { totalCount pageInfo { hasNextPage endCursor }
   nodes { id body url createdAt updatedAt author { login } }
  }
 } }
}"""


def _graphql_pages(collector, kind, query, variables, location):
    rows, seen, cursors = [], set(), set()
    cursor = None
    expected = None
    complete = False
    try:
        for _ in range(collector.max_pages):
            response = collector.reader.graphql(query, {**variables, "cursor": cursor})
            connection = response["data"]
            for part in location:
                connection = connection[part]
            expected = connection["totalCount"]
            for row in connection["nodes"]:
                if (
                    not isinstance(row, dict)
                    or not isinstance(row.get("id"), str)
                    or not row["id"]
                    or row["id"] in seen
                ):
                    raise CollectionError("graphql_duplicate_or_missing_id")
                seen.add(row["id"])
                rows.append(row)
            page = connection["pageInfo"]
            if page.get("hasNextPage") is False:
                complete = True
                break
            cursor = page.get("endCursor")
            if not cursor or cursor in cursors:
                raise CollectionError("graphql_cursor_invalid")
            cursors.add(cursor)
        if not complete:
            raise CollectionError("pagination_limit_exceeded")
        if type(expected) is not int or expected != len(rows):
            complete = False
            collector.gap(
                kind,
                str(variables),
                "inventory_count_mismatch",
                {"expected": expected, "collected": len(rows)},
            )
    except (CollectionError, KeyError, TypeError, AttributeError) as error:
        reason = str(error) if isinstance(error, CollectionError) else "graphql_shape_invalid"
        collector.gap(
            kind,
            kind + ":" + str(variables.get("id", "repository")),
            reason,
            {"collected": len(rows)},
        )
    collector.inventory[kind + ":" + str(variables.get("id", "repository"))] = {
        "complete": complete,
        "count": len(rows),
        "expected": expected,
    }
    return rows


def _discussions(collector):
    owner, name = collector.repository.split("/")
    discussions = _graphql_pages(
        collector,
        "discussion",
        _DISCUSSIONS,
        {"owner": owner, "name": name},
        ("repository", "discussions"),
    )
    for discussion in discussions:
        collector.record("discussion", discussion)
        comments = _graphql_pages(
            collector,
            "discussion_comment",
            _COMMENTS,
            {"id": discussion["id"]},
            ("node", "comments"),
        )
        for comment in comments:
            collector.record("discussion_comment", {**comment, "discussion_id": discussion["id"]})
            replies = (
                _graphql_pages(
                    collector,
                    "discussion_reply",
                    _REPLIES,
                    {"id": comment["id"]},
                    ("node", "replies"),
                )
                if comment.get("replies", {}).get("totalCount")
                else []
            )
            for reply in replies:
                collector.record(
                    "discussion_reply",
                    {
                        **reply,
                        "discussion_id": discussion["id"],
                        "parent_comment_id": comment["id"],
                    },
                )


def collect_github(
    repository: str,
    token: str,
    sink,
    *,
    reader=None,
    max_requests: int = MAX_REQUESTS,
    max_total_bytes: int = MAX_TOTAL_BYTES,
    max_download_bytes: int = MAX_DOWNLOAD_BYTES,
    max_pages: int = MAX_PAGES,
) -> dict:
    """Collect accessible evidence; report outstanding sources without generation.

    Re-run with the same private cache to resume immutable completed attempts and
    artifact downloads. Mutable inventories are still checked against GitHub.
    """
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("repository_name_invalid")
    if type(max_pages) is not int or max_pages < 1:
        raise ValueError("github_collection_limit_invalid")
    reader = reader or GitHubReader(
        repository,
        token,
        sink.cache_dir,
        max_requests=max_requests,
        max_total_bytes=max_total_bytes,
        max_download_bytes=max_download_bytes,
    )
    scope = hashlib.sha256((repository + "\0" + token).encode()).hexdigest()
    progress_dir = getattr(reader, "cache_dir", Path(sink.cache_dir) / "github" / scope)
    collector = _Collector(
        repository, reader, sink, max_pages, _Progress(Path(progress_dir), repository)
    )
    inventories = _collect_inventories(collector)
    # A denied GraphQL API is a visible gap, not an empty discussion set.
    _discussions(collector)
    _collect_attachments(collector)
    # Reserve progress for every expensive family so Actions cannot starve artifacts.
    _collect_reviews(collector, inventories["pull_request"])
    _collect_runs(collector, inventories["actions_run"])
    _collect_artifacts(collector, inventories["actions_artifact_metadata"])
    _collect_releases(collector, inventories["release"])
    # Include newly discovered review-body links and additional parent relations.
    # Already attempted failures are reported without another network attempt.
    _collect_attachments(collector)
    return _collection_summary(collector)


def _collect_attachments(collector):
    for url, parents in collector.attachments.items():
        if collector.attachment_parents_recorded.get(url) == set(parents):
            continue
        metadata = {
            "repository": collector.repository,
            "parent_sources": [parents[key] for key in sorted(parents)],
            "access_scope": "public_github_attachment",
            "name": urlsplit(url).path.rsplit("/", 1)[-1],
        }
        try:
            if url in collector.attachment_failures:
                raise CollectionError(collector.attachment_failures[url])
            raw = collector.reader.attachment(url)
            if urlsplit(url).path.lower().endswith(".zip") and not raw.startswith(b"PK"):
                raise CollectionError("download_zip_invalid")
            metadata["content_type"] = (
                "application/zip" if raw.startswith(b"PK") else "application/octet-stream"
            )
            collector.sink.add(
                "github_attachment",
                f"github:{collector.repository}:attachment:{url}",
                url,
                hashlib.sha256(raw).hexdigest(),
                raw,
                metadata,
            )
            collector.counts["github_attachment"] = collector.counts.get("github_attachment", 0) + 1
        except CollectionError as error:
            collector.attachment_failures[url] = str(error)
            collector.gap("github_attachment", url, str(error), metadata)
        collector.attachment_parents_recorded[url] = set(parents)


def _collect_inventories(collector):
    repository_metadata = collector.read_object("repository", "")
    if repository_metadata and repository_metadata.get("has_wiki"):
        collector.gap(
            "wiki", collector.repository + ".wiki.git", "separate_wiki_repository_not_collected"
        )
    inventories = {}
    specs = (
        ("issue", "/issues?state=all&sort=created&direction=asc", None),
        ("pull_request", "/pulls?state=all&sort=created&direction=asc", None),
        ("issue_comment", "/issues/comments?sort=created&direction=asc", None),
        ("review_comment", "/pulls/comments?sort=created&direction=asc", None),
        ("issue_event", "/issues/events", None),
        ("actions_run", "/actions/runs", "workflow_runs"),
        ("actions_artifact_metadata", "/actions/artifacts", "artifacts"),
        ("release", "/releases", None),
    )
    for kind, path, key in specs:
        rows = collector.pages(kind, path, key=key)
        inventories[kind] = rows
        for row in rows:
            collector.record(kind, row)
    return inventories


def _collect_reviews(collector, rows):
    reader = collector.reader
    pulls = collector.resume("pull_request_reviews", rows)
    review_limit = collector.phase_limit(0.25)
    for index, pull in enumerate(pulls):
        if getattr(reader, "stop_reason", None):
            collector.deferred("pull_request_reviews", pulls[index:])
            break
        if collector.phase_exhausted(review_limit):
            collector.deferred(
                "pull_request_reviews", pulls[index:], reason="detail_phase_budget_exhausted"
            )
            break
        number = pull.get("number")
        if type(number) is not int or number < 1:
            collector.gap(
                "pull_request_reviews", str(pull.get("id")), "pull_request_number_invalid"
            )
            collector.advance("pull_request_reviews", pull)
            continue
        reviews = collector.pages("pull_request_review", f"/pulls/{number}/reviews")
        for review in reviews:
            collector.record("pull_request_review", {**review, "pull_request_number": number})
        collector.advance("pull_request_reviews", pull)


def _collect_runs(collector, rows):
    reader = collector.reader
    runs = collector.resume("actions_run_details", rows)
    run_limit = collector.phase_limit(0.45)
    for index, run in enumerate(runs):
        if getattr(reader, "stop_reason", None):
            collector.deferred("actions_run_details", runs[index:])
            break
        if collector.phase_exhausted(run_limit):
            collector.deferred(
                "actions_run_details", runs[index:], reason="detail_phase_budget_exhausted"
            )
            break
        attempts = run.get("run_attempt")
        run_id = run["id"]
        if type(attempts) is not int or not 1 <= attempts <= 10000:
            collector.gap("actions_run_details", str(run_id), "run_attempt_count_invalid")
            collector.advance("actions_run_details", run)
            continue
        for attempt in range(1, attempts + 1):
            path = f"/actions/runs/{run_id}/attempts/{attempt}"
            immutable = attempt < attempts or run.get("status") == "completed"
            detail = collector.read_object("actions_attempt", path, immutable=immutable)
            jobs = collector.pages("actions_job", path + "/jobs", key="jobs", immutable=immutable)
            for job in jobs:
                collector.record("actions_job", {**job, "run_attempt": attempt})
            if detail is not None:
                collector.download(
                    "actions_logs",
                    path + "/logs",
                    {**detail, "name": f"run-{run_id}-attempt-{attempt}.zip"},
                    immutable=immutable,
                )
            if getattr(reader, "stop_reason", None):
                collector.gap(
                    "actions_run_details",
                    str(run_id),
                    reader.stop_reason,
                    {"attempt": attempt, "last_attempt": attempts},
                )
                break
        collector.advance("actions_run_details", run)


def _collect_artifacts(collector, rows):
    reader = collector.reader
    artifacts = collector.resume("actions_artifact", rows)
    artifact_limit = collector.phase_limit(0.9)
    for index, artifact in enumerate(artifacts):
        if getattr(reader, "stop_reason", None):
            collector.deferred("actions_artifact", artifacts[index:])
            break
        if collector.phase_exhausted(artifact_limit):
            collector.deferred(
                "actions_artifact", artifacts[index:], reason="detail_phase_budget_exhausted"
            )
            break
        collector.download("actions_artifact", f"/actions/artifacts/{artifact['id']}/zip", artifact)
        collector.advance("actions_artifact", artifact)


def _collect_releases(collector, rows):
    reader = collector.reader
    releases = collector.resume("release_assets", rows)
    for index, release in enumerate(releases):
        if getattr(reader, "stop_reason", None):
            collector.deferred("release_assets", releases[index:])
            break
        assets = collector.pages("release_asset_metadata", f"/releases/{release['id']}/assets")
        for asset in assets:
            collector.record("release_asset_metadata", asset)
            collector.download(
                "release_asset", f"/releases/assets/{asset['id']}", asset, immutable=False
            )
        collector.advance("release_assets", release)


def _collection_summary(collector):
    reader = collector.reader
    collector.gap(
        "provider_export",
        "provider-input-output-tool-events",
        "external_provider_exports_not_supplied",
    )
    collector.gap(
        "conversation_export",
        "authorized-conversation-exports",
        "external_conversation_exports_not_supplied",
    )
    return {
        "status": "partial" if collector.gaps else "collected",
        "repository": collector.repository,
        "counts": collector.counts,
        "inventories": collector.inventory,
        "gap_count": collector.gaps,
        "requests": getattr(reader, "requests", None),
        "downloaded_bytes": getattr(reader, "downloaded_bytes", None),
        "cache_hits": getattr(reader, "cache_hits", None),
        "generation_requests": 0,
    }
