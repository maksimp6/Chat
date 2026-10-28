"""Read authenticated project sources through the existing native tool executor."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from urllib.parse import parse_qsl, urljoin, urlsplit

import requests

from scripts.logos_knowledge_host import bound
from scripts.semantic_pipeline import _redact_repository_text

_FIELDS = "id body url createdAt updatedAt author { login }"
_PAGE = "pageInfo { hasNextPage endCursor }"
_DISCUSSION = (
    "query($owner:String!,$name:String!,$number:Int!,$after:String){"
    "repository(owner:$owner,name:$name){nameWithOwner discussion(number:$number){"
    "id number title body url createdAt updatedAt author{login} "
    f"comments(first:100,after:$after){{nodes{{{_FIELDS}}} {_PAGE}}}" + "}}}"
)
_REPLIES = (
    "query($id:ID!,$after:String){node(id:$id){... on DiscussionComment{"
    "discussion{number repository{nameWithOwner}} "
    f"replies(first:100,after:$after){{nodes{{{_FIELDS}}} {_PAGE}}}" + "}}}"
)


def _path(value: str) -> str:
    if not isinstance(value, str) or any(ord(char) < 32 for char in value):
        raise ValueError("Expected a repository-relative API path")
    parts = urlsplit(value)
    if parts.scheme or parts.netloc or parts.fragment or value.startswith("/"):
        raise ValueError("Absolute URLs and fragments are not project API paths")
    if not re.fullmatch(r"[A-Za-z0-9_./-]+", parts.path) or any(
        part in {"", ".", ".."} for part in parts.path.split("/")
    ):
        raise ValueError("Invalid project API path")
    query = parse_qsl(parts.query, keep_blank_values=True, strict_parsing=True)
    allowed = {
        "page",
        "per_page",
        "state",
        "filter",
        "status",
        "branch",
        "event",
        "head_sha",
        "created",
        "since",
        "sort",
        "direction",
        "labels",
        "ref",
        "before",
        "after",
        "actor",
        "check_suite_id",
        "exclude_pull_requests",
    }
    if len({key for key, _ in query}) != len(query) or any(
        key not in allowed for key, _ in query
    ):
        raise ValueError("Unsupported project API query")
    for key, item in query:
        if key in {"page", "per_page"} and (
            not item.isascii()
            or not item.isdigit()
            or int(item) < 1
            or (key == "per_page" and int(item) > 100)
        ):
            raise ValueError("Use positive pages and per_page no greater than 100")
    return value


def _redirect_allowed(url: str, api_root: str) -> bool:
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.port not in {None, 443}
    ):
        return False
    return url.startswith(api_root) or host.endswith(
        (".githubusercontent.com", ".blob.core.windows.net")
    )


def _response(session, method, url, headers, **kwargs):
    try:
        response = session.request(
            method, url, headers=headers, timeout=60, allow_redirects=False, **kwargs
        )
        if response.status_code >= 400:
            raise RuntimeError(f"github_project_http_{response.status_code}")
        return response
    except requests.RequestException:
        raise RuntimeError("github_project_transport_failed") from None


def _pages(fetch, first: dict, field: str) -> list[dict]:
    items, seen, cursors = [], set(), set()
    page = first
    while True:
        connection = page[field]
        for item in connection["nodes"]:
            if item["id"] in seen:
                raise ValueError("Repeated discussion comment")
            seen.add(item["id"])
            items.append(item)
        info = connection["pageInfo"]
        if info["hasNextPage"] is False:
            return items
        cursor = info["endCursor"]
        if not isinstance(cursor, str) or not cursor or cursor in cursors:
            raise ValueError("Incomplete discussion pagination")
        cursors.add(cursor)
        page = fetch(cursor)


class _ProjectReader:
    def __init__(self, token, repository, session):
        self.token, self.repository, self.session = token, repository, session
        self.api_root = f"https://api.github.com/repos/{repository}/"
        self.headers = {"Accept": "application/vnd.github+json"}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"

    def result(self, value, source, **metadata):
        raw = json.dumps(value, ensure_ascii=False)
        safe, redactions = _redact_repository_text(
            raw.replace(self.token, "[REDACTED]") if self.token else raw
        )
        return {
            "source": source,
            "read_at": datetime.now(UTC).isoformat(),
            "source_sha256": hashlib.sha256(raw.encode()).hexdigest(),
            "content_sha256": hashlib.sha256(safe.encode()).hexdigest(),
            "hash_scope": "decoded_content_serialized_as_UTF8_JSON",
            "redactions": redactions + (raw.count(self.token) if self.token else 0),
            "authenticated": bool(self.token),
            "content": json.loads(safe),
            "truncated": False,
            **metadata,
        }

    def read_project(self, args):
        source = self.api_root + _path(args["path"])
        url, auth, seen = source, self.headers, set()
        while True:
            if url in seen:
                raise ValueError("GitHub redirect loop")
            seen.add(url)
            response = _response(self.session, "GET", url, auth)
            if response.status_code not in {301, 302, 303, 307, 308}:
                break
            url = urljoin(url, response.headers.get("Location", ""))
            if not _redirect_allowed(url, self.api_root):
                raise ValueError(
                    "GitHub redirect destination is not an allowed log source"
                )
            auth = (
                self.headers
                if url.startswith(self.api_root)
                else {"Accept": "text/plain"}
            )
        try:
            text = response.content.decode("utf-8")
        except UnicodeError:
            raise ValueError(
                "Binary resource: use an Actions job text-log endpoint"
            ) from None
        value = (
            json.loads(text)
            if "json" in response.headers.get("Content-Type", "")
            else text
        )
        return self.result(
            value,
            source,
            pagination={
                "link": response.headers.get("Link"),
                "complete": False,
                "instruction": "Follow every next page until exhausted; a single REST page is not the full collection",
            },
        )

    def graphql(self, query, variables):
        response = _response(
            self.session,
            "POST",
            "https://api.github.com/graphql",
            self.headers,
            json={"query": query, "variables": variables},
        )
        payload = response.json()
        if payload.get("errors") or not isinstance(payload.get("data"), dict):
            raise ValueError("GitHub discussion query failed or is incomplete")
        return payload["data"]

    def discussion(self, number, after):
        owner, name = self.repository.split("/")
        repo = self.graphql(
            _DISCUSSION,
            {"owner": owner, "name": name, "number": number, "after": after},
        )["repository"]
        if (
            not repo
            or repo.get("nameWithOwner") != self.repository
            or not repo.get("discussion")
            or repo["discussion"].get("number") != number
        ):
            raise ValueError("Discussion is missing from this project")
        return repo["discussion"]

    def replies(self, number, comment_id, after):
        node = self.graphql(_REPLIES, {"id": comment_id, "after": after})["node"]
        if (
            node["discussion"]["number"] != number
            or node["discussion"]["repository"]["nameWithOwner"] != self.repository
        ):
            raise ValueError("Discussion reply does not belong to this project")
        return node

    def read_discussion(self, args):
        number = args["number"]
        if type(number) is not int or number < 1:
            raise ValueError("Expected a positive discussion number")

        def discussion(after):
            return self.discussion(number, after)

        first = discussion(None)
        comments = _pages(discussion, first, "comments")
        for comment in comments:

            def replies(after, comment_id=comment["id"]):
                return self.replies(number, comment_id, after)

            comment["replies"] = _pages(replies, replies(None), "replies")
        first["comments"] = comments
        return self.result(
            first,
            f"https://github.com/{self.repository}/discussions/{number}",
            pagination={"complete": True},
        )


def register_project_reader(
    token: str, repository: str = "maksimp6/Chat", *, session=None, tool_registry=None
) -> None:
    """Register project reads; token is private runtime state, never a tool argument."""
    if not isinstance(token, str) or not re.fullmatch(
        r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository
    ):
        raise ValueError("Project reader requires a string token and owner/repository")
    reader = _ProjectReader(token.strip(), repository, session or requests.Session())
    target = tool_registry or bound.registry
    for name, func, properties, description in (
        (
            "github_read_project",
            reader.read_project,
            {"path": {"type": "string"}},
            "Read complete JSON/text from a repository-relative GitHub REST GET path, including Issues, PRs, Actions jobs and text logs. Use page/per_page<=100 and follow every pagination link. No writes or arbitrary URLs.",
        ),
        (
            "github_read_discussion",
            reader.read_discussion,
            {"number": {"type": "integer", "minimum": 1}},
            "Read a project GitHub Discussion with all comments and replies through fixed read-only queries. No arbitrary GraphQL, writes or mutation.",
        ),
    ):
        target.register(
            "project_knowledge",
            name,
            {
                "func": func,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": list(properties),
                    "additionalProperties": False,
                },
                "read_only": True,
                "requires_approval": False,
                "risk_level": "low",
            },
        )
