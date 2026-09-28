"""Deterministic Markdown mirror of external documentation (no LLM in this path).

Pipeline: seed URLs -> crawl within an allowed scope -> normalize/deduplicate ->
fetch -> strip navigation -> HTML to Markdown -> SHA-256 -> manifest.json.

Spec: docs/integrations/cloudru-docs-mirror.md (issue #428).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path, PurePosixPath
import re
import time
from typing import Callable, Iterable
from urllib.parse import urljoin, urlsplit, urlunsplit

MANIFEST_NAME = "manifest.json"
USER_AGENT = "AliceProDocsMirror/1.0 (+https://github.com/maksimp6/Chat)"

# First wave from the spec: Workflow Studio, Repo, Container Apps, Artifact
# Registry, IAM, API, Terraform, Managed PostgreSQL, Object Storage, networking.
CLOUDRU_FIRST_WAVE = (
    "pipeline",
    "repo-evolution",
    "container-apps-evolution",
    "artifact-registry-evolution",
    "administration",
    "console_api",
    "terraform-evolution",
    "paas-postgresql",
    "s3e",
    "evolution-vpc",
    "evolution-dns",
)


# URL handling ---------------------------------------------------------------


def normalize_url(url: str, *, base: str | None = None) -> str | None:
    """Canonical form: https, lowercase host, no query/fragment, no .html, no trailing slash.

    Cloud.ru serves the same page under ``/ru/docs/...`` and ``/docs/...`` and with or
    without ``.html``; both collapse to one canonical URL.
    """
    if base:
        url = urljoin(base, url)
    parts = urlsplit(url.strip())
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return None
    host = parts.netloc.lower().removeprefix("www.")
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if path.startswith("/ru/docs/") or path == "/ru/docs":
        path = path[3:]
    path = path.removesuffix(".html")
    path = path.rstrip("/") or "/"
    return urlunsplit(("https", host, path, "", ""))


def in_scope(url: str, host: str, prefixes: Iterable[str]) -> bool:
    parts = urlsplit(url)
    if parts.netloc != host:
        return False
    return any(
        parts.path == p.rstrip("/") or parts.path.startswith(p.rstrip("/") + "/") for p in prefixes
    )


def url_to_path(url: str, strip_prefix: str = "/docs") -> str:
    """Safe relative Markdown path for a canonical URL (never escapes the mirror root)."""
    path = urlsplit(url).path
    if path.startswith(strip_prefix + "/"):
        path = path[len(strip_prefix) + 1 :]
    elif path == strip_prefix:
        path = ""
    segments = [re.sub(r"[^A-Za-z0-9._-]", "_", s) for s in path.split("/") if s]
    segments = [s for s in segments if s not in {".", ".."}]
    if not segments:
        segments = ["index"]
    return str(PurePosixPath(*segments)) + ".md"


# HTML -> Markdown -----------------------------------------------------------

_SKIP_TAGS = {
    "script",
    "style",
    "noscript",
    "nav",
    "header",
    "footer",
    "aside",
    "svg",
    "form",
    "button",
}
_BLOCK_TAGS = {"p", "div", "section", "article", "main", "blockquote", "figure", "dl"}
_VOID = {"br", "img", "hr", "meta", "link", "input", "source", "col", "area", "base", "wbr"}


class _MarkdownParser(HTMLParser):
    def __init__(self, page_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self.out: list[str] = []
        self.links: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False
        self._pre = 0
        self._lists: list[list] = []  # [kind, counter]
        self._href: list[str | None] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._table: list[list[str]] | None = None
        self._main_depth: int | None = None
        self._depth = 0
        self.main_text: list[str] | None = None

    # Collect into the innermost open cell when inside a table.
    def _emit(self, text: str) -> None:
        if self._cell is not None:
            self._cell.append(text)
        else:
            self.out.append(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag not in _VOID:
            self._depth += 1
        if tag == "a" and attrs.get("href"):
            self.links.append(attrs["href"])
        if self._skip:
            if tag not in _VOID:
                self._skip += 1
            return
        if tag in _SKIP_TAGS:
            self._skip = 1
            return
        if tag == "title":
            self._in_title = True
        elif tag in {"main", "article"} and self._main_depth is None:
            self._main_depth = self._depth
            self._main_start = len(self.out)
        elif re.fullmatch(r"h[1-6]", tag):
            self._emit("\n\n" + "#" * int(tag[1]) + " ")
        elif tag in _BLOCK_TAGS and not self._pre:
            self._emit("\n\n")
        elif tag == "br":
            self._emit("\n")
        elif tag == "hr":
            self._emit("\n\n---\n\n")
        elif tag in {"ul", "ol"}:
            self._lists.append([tag, 0])
            self._emit("\n")
        elif tag == "li":
            indent = "  " * max(len(self._lists) - 1, 0)
            if self._lists and self._lists[-1][0] == "ol":
                self._lists[-1][1] += 1
                self._emit(f"\n{indent}{self._lists[-1][1]}. ")
            else:
                self._emit(f"\n{indent}- ")
        elif tag == "pre":
            self._pre += 1
            self._emit("\n\n```\n")
        elif tag == "code" and not self._pre:
            self._emit("`")
        elif tag in {"strong", "b"}:
            self._emit("**")
        elif tag in {"em", "i"}:
            self._emit("*")
        elif tag == "a":
            href = attrs.get("href")
            target = normalize_url(href, base=self.page_url) if href else None
            self._href.append(target or href)
            self._emit("[")
        elif tag == "img":
            alt = (attrs.get("alt") or "").strip()
            src = attrs.get("src")
            if src:
                self._emit(f"![{alt}]({urljoin(self.page_url, src)})")
        elif tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag not in _VOID:
            self._depth -= 1
        if self._skip:
            self._skip -= 1
            return
        if tag == "title":
            self._in_title = False
        elif (
            tag in {"main", "article"}
            and self._main_depth == self._depth + 1
            and self.main_text is None
        ):
            self.main_text = self.out[self._main_start :]
        elif re.fullmatch(r"h[1-6]", tag) or (tag in _BLOCK_TAGS and not self._pre):
            self._emit("\n\n")
        elif tag in {"ul", "ol"} and self._lists:
            self._lists.pop()
            self._emit("\n")
        elif tag == "pre" and self._pre:
            self._pre -= 1
            self._emit("\n```\n\n")
        elif tag == "code" and not self._pre:
            self._emit("`")
        elif tag in {"strong", "b"}:
            self._emit("**")
        elif tag in {"em", "i"}:
            self._emit("*")
        elif tag == "a" and self._href:
            href = self._href.pop()
            self._emit(f"]({href})" if href else "]")
        elif tag in {"td", "th"} and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()).replace("|", "\\|"))
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            rows, self._table = self._table, None
            if rows:
                width = max(len(r) for r in rows)
                rows = [r + [""] * (width - len(r)) for r in rows]
                lines = ["| " + " | ".join(rows[0]) + " |", "|" + " --- |" * width]
                lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
                self._emit("\n\n" + "\n".join(lines) + "\n\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self._skip:
            return
        if self._pre:
            self._emit(data)
        else:
            text = re.sub(r"\s+", " ", data)
            if text.strip() or (text and self.out and not self.out[-1].endswith((" ", "\n"))):
                self._emit(text)


def html_to_markdown(html: str, page_url: str) -> tuple[str, str, list[str]]:
    """Return (title, markdown, raw hrefs). Uses <main>/<article> when the page has one."""
    parser = _MarkdownParser(page_url)
    parser.feed(html)
    parser.close()
    body = "".join(parser.main_text if parser.main_text is not None else parser.out)
    lines = [line.rstrip() for line in body.splitlines()]
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return " ".join(parser.title.split()), text + "\n", parser.links


def render_page(url: str, title: str, markdown: str) -> str:
    """Stable file content. No fetch time in the file, so unchanged pages hash the same."""
    header = f"<!-- source: {url} -->\n"
    if title and not markdown.lstrip().startswith("# "):
        header += f"\n# {title}\n"
    return header + "\n" + markdown


# Manifest + crawl -----------------------------------------------------------


@dataclass
class FetchResult:
    status: int
    text: str = ""
    content_type: str = "text/html"
    last_modified: str | None = None
    final_url: str | None = None


Fetcher = Callable[[str], FetchResult]


def requests_fetcher(timeout: float = 20.0) -> Fetcher:
    import requests

    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    def fetch(url: str) -> FetchResult:
        response = session.get(url, timeout=timeout)
        response.encoding = response.encoding or "utf-8"
        return FetchResult(
            status=response.status_code,
            text=response.text if response.ok else "",
            content_type=response.headers.get("Content-Type", ""),
            last_modified=response.headers.get("Last-Modified"),
            final_url=response.url,
        )

    return fetch


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class CrawlStats:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    errors: int = 0
    missing: int = 0
    events: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            k: getattr(self, k) for k in ("created", "updated", "unchanged", "errors", "missing")
        }


class Mirror:
    def __init__(
        self,
        root: Path,
        *,
        host: str,
        prefixes: Iterable[str],
        fetcher: Fetcher,
        strip_prefix: str = "/docs",
        delay_s: float = 0.5,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.root = Path(root)
        self.host = host
        self.prefixes = tuple(prefixes)
        self.fetch = fetcher
        self.strip_prefix = strip_prefix
        self.delay_s = delay_s
        self._sleep = sleep
        self.manifest_path = self.root / MANIFEST_NAME
        self.entries: dict[str, dict] = {}
        if self.manifest_path.exists():
            data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            self.entries = {e["canonical_url"]: e for e in data.get("pages", [])}

    def save_manifest(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        pages = sorted(self.entries.values(), key=lambda e: e["canonical_url"])
        payload = {
            "generator": "docs_mirror",
            "host": self.host,
            "prefixes": list(self.prefixes),
            "pages": pages,
        }
        tmp = self.manifest_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.manifest_path)

    def _target(self, rel_path: str) -> Path:
        target = (self.root / rel_path).resolve()
        if self.root.resolve() not in target.parents:
            raise ValueError(f"unsafe mirror path: {rel_path}")
        return target

    def crawl(self, seeds: Iterable[str], *, max_pages: int = 5000) -> CrawlStats:
        stats = CrawlStats()
        queue: list[str] = []
        seen: set[str] = set()
        for seed in seeds:
            canonical = normalize_url(seed)
            if canonical and canonical not in seen:
                seen.add(canonical)
                queue.append(canonical)
        visited: set[str] = set()
        while queue and len(visited) < max_pages:
            url = queue.pop(0)
            visited.add(url)
            links = self._process(url, stats)
            for href in links:
                canonical = normalize_url(href, base=url)
                if (
                    canonical
                    and canonical not in seen
                    and in_scope(canonical, self.host, self.prefixes)
                ):
                    seen.add(canonical)
                    queue.append(canonical)
            if queue and self.delay_s:
                self._sleep(self.delay_s)
        # Pages in scope that we knew about but no longer reach are marked, never deleted.
        if not queue:
            for url, entry in self.entries.items():
                if (
                    url not in visited
                    and in_scope(url, self.host, self.prefixes)
                    and entry.get("status") == "ok"
                ):
                    entry["status"] = "missing"
                    entry["checked_at"] = _now()
                    stats.missing += 1
        self.save_manifest()
        return stats

    def _process(self, url: str, stats: CrawlStats) -> list[str]:
        rel_path = url_to_path(url, self.strip_prefix)
        entry = self.entries.get(url) or {"url": url, "canonical_url": url, "path": rel_path}
        started = time.monotonic()
        try:
            result = self.fetch(url)
        except Exception as exc:  # network errors must not stop the crawl
            result = FetchResult(status=0, text="")
            error = type(exc).__name__
        else:
            error = None if 200 <= result.status < 300 else f"HTTP {result.status}"
        entry["checked_at"] = _now()
        entry["http_status"] = result.status
        if error or "html" not in (result.content_type or "html"):
            entry["error"] = error or f"unsupported content type {result.content_type}"
            if result.status in {404, 410}:
                entry["status"] = "missing" if entry.get("sha256") else "error"
            else:
                entry.setdefault("status", "error")
            self.entries[url] = entry
            stats.errors += 1
            stats.events.append({"url": url, "result": "error", "error": entry["error"]})
            return []
        title, markdown, links = html_to_markdown(result.text, url)
        content = render_page(url, title, markdown)
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        target = self._target(rel_path)
        if entry.get("sha256") == digest and target.exists():
            outcome = "unchanged"
            stats.unchanged += 1
        else:
            outcome = "updated" if entry.get("sha256") else "created"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            entry["previous_sha256"] = entry.get("sha256")
            entry["sha256"] = digest
            entry["fetched_at"] = entry["checked_at"]
            setattr(stats, outcome, getattr(stats, outcome) + 1)
        entry.update(
            {
                "path": rel_path,
                "title": title,
                "status": "ok",
                "content_length": len(content.encode("utf-8")),
                "last_modified": result.last_modified,
            }
        )
        entry.pop("error", None)
        self.entries[url] = entry
        stats.events.append(
            {"url": url, "result": outcome, "ms": round((time.monotonic() - started) * 1000)}
        )
        return links
