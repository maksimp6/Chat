"""Web search with parsed results: DuckDuckGo (HTML and Lite), Bing, both, or a custom site.

Results come back as text the model can use directly: rank, title, address and
snippet. Advertising blocks are not included and their number is reported.
DuckDuckGo pages beyond the first are reached through the page's own "next"
form; the request of each page is remembered per query so a later page does not
walk the chain again.
"""

from __future__ import annotations

import base64
import binascii
import logging
import re
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any, Callable
from urllib.parse import parse_qs, quote_plus, urlencode, urljoin, urlsplit

from browser import css
from browser.dom import Document, Element, normalize_space, parse_html
from browser.errors import (
    E_SEARCH_BLOCKED,
    E_SEARCH_CUSTOM,
    E_SEARCH_PAGE_SIZE,
    BrowserError,
    BrowserNetworkError,
    Deadline,
)
from browser.forms import build_submission
from browser.http_engine import LoadedDocument, RequestSpec, encode_submission, origin_of
from browser.page import PageModel
from browser.redact import display_url

logger = logging.getLogger("logos.browser.search")

ENGINES = ("duckduckgo", "duckduckgo_lite", "bing", "all", "custom")
SAFE_SEARCH = ("off", "moderate", "strict")
TIME_RANGES = ("day", "week", "month", "year")

_DDG_HTML = "https://html.duckduckgo.com/html/"
_DDG_LITE = "https://lite.duckduckgo.com/lite/"
_BING = "https://www.bing.com/search"
_DDG_SAFE = {"strict": "1", "moderate": "-1", "off": "-2"}
_DDG_TIME = {"day": "d", "week": "w", "month": "m", "year": "y"}
_BING_TIME = {"day": 'ex1:"ez1"', "week": 'ex1:"ez2"', "month": 'ex1:"ez3"'}
_REGION = re.compile(r"^([A-Za-z]{2,3})[-_]([A-Za-z]{2})$")
_DDG_BLOCK_MARKERS = re.compile(r"anomaly[-_](?:modal|js)|/anomaly\.js|challenge-form", re.I)
_ENGINE_TITLES = {
    "duckduckgo": "DuckDuckGo",
    "duckduckgo_lite": "DuckDuckGo Lite",
    "bing": "Bing",
    "custom": "Поисковик",
}


@dataclass
class SearchResult:
    rank: int
    title: str
    url: str
    snippet: str
    display_url: str
    engines: tuple[str, ...]


@dataclass
class CustomEngine:
    url_template: str
    result_selector: str
    title_selector: str | None = None
    link_selector: str | None = None
    snippet_selector: str | None = None
    page_size: int | None = None


@dataclass
class SearchOutcome:
    engine: str
    query: str
    page: int
    results: list[SearchResult] = field(default_factory=list)
    ads_skipped: int = 0
    has_next: bool = False
    notes: list[str] = field(default_factory=list)
    documents: list[LoadedDocument] = field(default_factory=list)


@dataclass
class _Parsed:
    results: list[SearchResult]
    ads: int
    has_next: bool
    next_spec: RequestSpec | None
    document: LoadedDocument
    next_offset: int | None = None


Fetch = Callable[[RequestSpec], LoadedDocument]


# ---------------------------------------------------------------------------
# Helpers


def _region(region: str | None) -> tuple[str, str] | None:
    if not region:
        return None
    match = _REGION.match(region.strip())
    if not match:
        raise BrowserError(
            f"region «{region}»: ожидается Язык-СТРАНА, например ru-RU или en-US"
        )
    return match.group(1).lower(), match.group(2).upper()


def _document(doc: LoadedDocument) -> Document:
    return parse_html(doc.text or "", xml_mode=False)


def _text(element: Element | None) -> str:
    return normalize_space(element.text()) if element is not None else ""


def _get(fetch: Fetch, url: str, deadline: Deadline, referrer: str | None = None) -> LoadedDocument:
    return fetch(
        RequestSpec(
            url=url,
            method="GET",
            mode="navigate",
            referrer=referrer,
            user_initiated=True,
            deadline=deadline,
        )
    )


def _host_label(url: str) -> str:
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    host = parts.hostname or ""
    path = parts.path if parts.path not in ("", "/") else ""
    return (host + path) or url


def _normalized(url: str) -> str:
    try:
        parts = urlsplit(url)
    except ValueError:
        return url.strip().lower()
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = parts.path.rstrip("/")
    query = f"?{parts.query}" if parts.query else ""
    return f"{host}{path}{query}"


def _check_status(doc: LoadedDocument, engine: str) -> None:
    title = _ENGINE_TITLES.get(engine, engine)
    if doc.status is not None and doc.status >= 400:
        raise BrowserNetworkError(
            f"{title} ответил {doc.status} {doc.reason}".rstrip()
            + " — результатов нет. Попробуйте позже или другой engine"
        )
    if doc.kind == "binary":
        raise BrowserError(f"{title} вернул не страницу результатов, а файл ({doc.mime})")


def _next_form_spec(
    doc: LoadedDocument,
    document: Document,
    current_offset: int,
    deadline: Deadline,
) -> tuple[RequestSpec, int] | None:
    """The request of the page's "next" form: the form whose s field is the smallest above ours."""
    best: tuple[int, Element, Element | None] | None = None
    for form in css.select(document, "form"):
        offset_field = None
        submit = None
        for element in form.descendants():
            if element.tag != "input":
                continue
            kind = (element.get("type") or "text").strip().lower()
            if element.get("name") == "s" and offset_field is None:
                offset_field = element
            if kind in ("submit", "image") and submit is None:
                submit = element
        if offset_field is None:
            continue
        try:
            value = int((offset_field.get("value") or "0").strip())
        except ValueError:
            continue
        if value <= current_offset:
            continue
        if best is None or value < best[0]:
            best = (value, form, submit)
    if best is None:
        return None
    model = PageModel(document, url=doc.url, engine="http", charset=doc.charset or "utf-8")
    submission = build_submission(model, best[1], best[2])
    spec = encode_submission(
        submission,
        referrer=doc.url,
        origin=origin_of(doc.url),
        deadline=deadline,
        max_redirects=0,
        max_bytes=0,
    )
    return spec, best[0]


# ---------------------------------------------------------------------------
# DuckDuckGo


def _ddg_params(query: str, region: tuple[str, str] | None, safe: str | None, time_range: str | None) -> list[tuple[str, str]]:
    params = [("q", query)]
    if region:
        params.append(("kl", f"{region[1].lower()}-{region[0]}"))
    if safe:
        params.append(("kp", _DDG_SAFE[safe]))
    if time_range:
        params.append(("df", _DDG_TIME[time_range]))
    return params


def _ddg_target(href: str | None, base: str) -> str:
    absolute = urljoin(base, (href or "").strip())
    try:
        parts = urlsplit(absolute)
    except ValueError:
        return absolute
    if parts.path.startswith("/l/"):
        target = parse_qs(parts.query).get("uddg")
        if target and target[0]:
            return target[0]
    return absolute


def _ddg_is_ad_link(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    return host.endswith("duckduckgo.com") and parts.path.startswith(("/y.js", "/aclick"))


def _ddg_blocked(doc: LoadedDocument, document: Document) -> bool:
    if css.select_one(document, "form#challenge-form, .anomaly-modal, #challenge-form") is not None:
        return True
    return bool(_DDG_BLOCK_MARKERS.search(doc.text or ""))


def _parse_ddg_html(doc: LoadedDocument, current_offset: int, deadline: Deadline) -> _Parsed:
    _check_status(doc, "duckduckgo")
    document = _document(doc)
    if _ddg_blocked(doc, document):
        raise BrowserError(E_SEARCH_BLOCKED.format(engine="DuckDuckGo"))
    results: list[SearchResult] = []
    ads = 0
    for block in css.select(document, "div.result"):
        if "result--ad" in block.classes:
            ads += 1
            continue
        link = css.select_one(block, "a.result__a")
        if link is None:
            continue
        url = _ddg_target(link.get("href"), doc.url)
        if _ddg_is_ad_link(url):
            ads += 1
            continue
        shown = _text(css.select_one(block, ".result__url")) or _host_label(url)
        results.append(
            SearchResult(
                rank=len(results) + 1,
                title=_text(link),
                url=url,
                snippet=_text(css.select_one(block, ".result__snippet")),
                display_url=shown,
                engines=("duckduckgo",),
            )
        )
    following = None
    if css.select_one(document, ".no-results") is None:
        following = _next_form_spec(doc, document, current_offset, deadline)
    return _Parsed(
        results=results,
        ads=ads,
        has_next=following is not None,
        next_spec=following[0] if following else None,
        document=doc,
        next_offset=following[1] if following else None,
    )


def _parse_ddg_lite(doc: LoadedDocument, current_offset: int, deadline: Deadline) -> _Parsed:
    _check_status(doc, "duckduckgo_lite")
    document = _document(doc)
    if _ddg_blocked(doc, document):
        raise BrowserError(E_SEARCH_BLOCKED.format(engine="DuckDuckGo Lite"))
    results: list[SearchResult] = []
    ads = 0
    current: dict[str, Any] | None = None
    entries: list[dict[str, Any]] = []
    for row in css.select(document, "tr"):
        link = css.select_one(row, "a.result-link")
        if link is not None:
            sponsored = any("sponsored" in cls for cls in row.classes) or (
                _ddg_is_ad_link(_ddg_target(link.get("href"), doc.url))
            )
            current = {
                "link": link,
                "ad": sponsored,
                "snippet": "",
                "shown": "",
            }
            entries.append(current)
            continue
        if current is None:
            continue
        snippet = css.select_one(row, "td.result-snippet")
        if snippet is not None and not current["snippet"]:
            current["snippet"] = _text(snippet)
        shown = css.select_one(row, "span.link-text")
        if shown is not None and not current["shown"]:
            current["shown"] = _text(shown)
    for entry in entries:
        if entry["ad"]:
            ads += 1
            continue
        url = _ddg_target(entry["link"].get("href"), doc.url)
        results.append(
            SearchResult(
                rank=len(results) + 1,
                title=_text(entry["link"]),
                url=url,
                snippet=entry["snippet"],
                display_url=entry["shown"] or _host_label(url),
                engines=("duckduckgo",),
            )
        )
    following = _next_form_spec(doc, document, current_offset, deadline)
    return _Parsed(
        results=results,
        ads=ads,
        has_next=following is not None,
        next_spec=following[0] if following else None,
        document=doc,
        next_offset=following[1] if following else None,
    )


def _ddg(
    fetch: Fetch,
    *,
    engine: str,
    query: str,
    page: int,
    region: tuple[str, str] | None,
    safe: str | None,
    time_range: str | None,
    cursors: dict,
    deadline: Deadline,
) -> tuple[_Parsed, list[str], list[LoadedDocument]]:
    base = _DDG_LITE if engine == "duckduckgo_lite" else _DDG_HTML
    parse = _parse_ddg_lite if engine == "duckduckgo_lite" else _parse_ddg_html
    key = (engine, query, region, safe, time_range)
    pages: dict[int, tuple[RequestSpec, int]] = cursors.setdefault(key, {})
    notes: list[str] = []
    documents: list[LoadedDocument] = []
    start = max((number for number in pages if number <= page), default=1)
    if start == 1:
        doc = _get(fetch, base + "?" + urlencode(_ddg_params(query, region, safe, time_range)), deadline)
        offset = 0
    else:
        stored_spec, offset = pages[start]
        doc = fetch(replace(stored_spec, deadline=deadline))
    documents.append(doc)
    parsed = parse(doc, offset, deadline)
    number = start
    while True:
        if parsed.next_spec is not None:
            pages[number + 1] = (parsed.next_spec, parsed.next_offset or 0)
        if number >= page:
            break
        if parsed.next_spec is None:
            notes.append(
                f"Страниц результатов меньше, чем {page}: последняя — {number}; показана она"
            )
            break
        number += 1
        doc = fetch(replace(parsed.next_spec, deadline=deadline))
        documents.append(doc)
        parsed = parse(doc, pages[number][1], deadline)
    return parsed, notes, documents


# ---------------------------------------------------------------------------
# Bing


def _bing_params(query: str, page: int, region: tuple[str, str] | None, safe: str | None, time_range: str | None) -> list[tuple[str, str]]:
    params = [("q", query), ("first", str((page - 1) * 10 + 1))]
    if region:
        params.extend([("mkt", f"{region[0]}-{region[1]}"), ("setlang", region[0]), ("cc", region[1])])
    if safe:
        params.append(("adlt", safe))
    if time_range:
        if time_range in _BING_TIME:
            params.append(("filters", _BING_TIME[time_range]))
        else:
            today = (date.today() - date(1970, 1, 1)).days
            params.append(("filters", f'ex1:"ez5_{today - 365}_{today}"'))
    return params


def _bing_target(href: str | None, base: str) -> str:
    absolute = urljoin(base, (href or "").strip())
    try:
        parts = urlsplit(absolute)
    except ValueError:
        return absolute
    host = (parts.hostname or "").lower()
    if host.endswith("bing.com") and parts.path.startswith("/ck/a"):
        encoded = (parse_qs(parts.query).get("u") or [""])[0]
        if encoded.startswith("a1"):
            payload = encoded[2:]
            payload += "=" * (-len(payload) % 4)
            try:
                decoded = base64.urlsafe_b64decode(payload.encode("ascii")).decode("utf-8")
            except (binascii.Error, ValueError, UnicodeError):
                decoded = ""
            if decoded.startswith(("http://", "https://")):
                return decoded
    return absolute


def _parse_bing(doc: LoadedDocument) -> _Parsed:
    _check_status(doc, "bing")
    document = _document(doc)
    try:
        path = urlsplit(doc.url).path
    except ValueError:
        path = ""
    if "/challenge" in path or css.select_one(document, "#b_captcha, .captcha, #captcha") is not None:
        raise BrowserError(E_SEARCH_BLOCKED.format(engine="Bing"))
    results: list[SearchResult] = []
    for item in css.select(document, "li.b_algo"):
        link = css.select_one(item, "h2 a")
        if link is None:
            continue
        url = _bing_target(link.get("href"), doc.url)
        snippet = ""
        for selector in (
            ".b_caption p",
            "p.b_lineclamp2",
            "p.b_lineclamp3",
            "p.b_lineclamp4",
            ".b_algoSlug",
            ".b_caption",
        ):
            snippet = _text(css.select_one(item, selector))
            if snippet:
                break
        shown = _text(css.select_one(item, "cite")) or _host_label(url)
        results.append(
            SearchResult(
                rank=len(results) + 1,
                title=_text(link),
                url=url,
                snippet=snippet,
                display_url=shown,
                engines=("bing",),
            )
        )
    ads = 0
    for block in css.select(document, "li.b_ad"):
        ads += max(1, len(css.select(block, "li")))
    has_next = css.select_one(document, "a.sb_pagN") is not None
    return _Parsed(results=results, ads=ads, has_next=has_next, next_spec=None, document=doc)


def _bing(
    fetch: Fetch,
    *,
    query: str,
    page: int,
    region: tuple[str, str] | None,
    safe: str | None,
    time_range: str | None,
    deadline: Deadline,
) -> tuple[_Parsed, list[str], list[LoadedDocument]]:
    url = _BING + "?" + urlencode(_bing_params(query, page, region, safe, time_range))
    doc = _get(fetch, url, deadline)
    return _parse_bing(doc), [], [doc]


# ---------------------------------------------------------------------------
# Custom


def _custom(
    fetch: Fetch, *, query: str, page: int, custom: CustomEngine | None, deadline: Deadline
) -> tuple[_Parsed, list[str], list[LoadedDocument]]:
    if custom is None or not custom.url_template or "{query}" not in custom.url_template or not custom.result_selector:
        raise BrowserError(E_SEARCH_CUSTOM)
    template = custom.url_template
    if "{offset}" in template and not custom.page_size:
        raise BrowserError(E_SEARCH_PAGE_SIZE)
    url = (
        template.replace("{query_raw}", query)
        .replace("{query}", quote_plus(query))
        .replace("{page}", str(page))
        .replace("{offset}", str((page - 1) * int(custom.page_size or 0)))
    )
    doc = _get(fetch, url, deadline)
    _check_status(doc, "custom")
    document = _document(doc)
    results: list[SearchResult] = []
    for block in css.select(document, custom.result_selector):
        title_el = css.select_one(block, custom.title_selector) if custom.title_selector else None
        title = _text(title_el) if title_el is not None else _text(block)
        if custom.link_selector:
            link_el = css.select_one(block, custom.link_selector)
        elif block.tag == "a" and block.get("href"):
            link_el = block
        else:
            link_el = css.select_one(block, "a[href]")
        href = None
        if link_el is not None:
            href = link_el.get("href") or link_el.get("data-href") or link_el.get("src")
            if not href:
                text = _text(link_el)
                href = text if re.match(r"(?i)^https?://", text) else None
        if not href:
            continue
        link_url = urljoin(doc.url, href.strip())
        snippet = ""
        if custom.snippet_selector:
            snippet = _text(css.select_one(block, custom.snippet_selector))
        results.append(
            SearchResult(
                rank=len(results) + 1,
                title=title,
                url=link_url,
                snippet=snippet,
                display_url=_host_label(link_url),
                engines=("custom",),
            )
        )
    paged = "{page}" in template or "{offset}" in template
    has_next = paged and bool(results) and (
        not custom.page_size or len(results) >= int(custom.page_size)
    )
    parsed = _Parsed(results=results, ads=0, has_next=has_next, next_spec=None, document=doc)
    return parsed, [], [doc]


# ---------------------------------------------------------------------------
# Entry point


def _run_one(
    fetch: Fetch,
    engine: str,
    *,
    query: str,
    page: int,
    region: tuple[str, str] | None,
    safe: str | None,
    time_range: str | None,
    custom: CustomEngine | None,
    cursors: dict,
    deadline: Deadline,
) -> tuple[_Parsed, list[str], list[LoadedDocument]]:
    if engine in ("duckduckgo", "duckduckgo_lite"):
        return _ddg(
            fetch,
            engine=engine,
            query=query,
            page=page,
            region=region,
            safe=safe,
            time_range=time_range,
            cursors=cursors,
            deadline=deadline,
        )
    if engine == "bing":
        return _bing(
            fetch,
            query=query,
            page=page,
            region=region,
            safe=safe,
            time_range=time_range,
            deadline=deadline,
        )
    return _custom(fetch, query=query, page=page, custom=custom, deadline=deadline)


def search(
    fetch: Fetch,
    *,
    query: str,
    engine: str,
    page: int | None = 1,
    region: str | None = None,
    safe_search: str | None = None,
    time_range: str | None = None,
    custom: CustomEngine | None = None,
    cursors: dict | None = None,
    deadline: Deadline | None = None,
) -> SearchOutcome:
    """Run one search and parse its results; raises BrowserError on a blocked or failed engine."""
    text = (query or "").strip()
    if not text:
        raise BrowserError("Укажите query — что искать")
    if engine not in ENGINES:
        raise BrowserError(f"Неизвестный engine «{engine}»; доступны: {', '.join(ENGINES)}")
    if safe_search is not None and safe_search not in SAFE_SEARCH:
        raise BrowserError(f"safe_search: ожидается {', '.join(SAFE_SEARCH)}")
    if time_range is not None and time_range not in TIME_RANGES:
        raise BrowserError(f"time_range: ожидается {', '.join(TIME_RANGES)}")
    number = int(page or 1)
    if number < 1:
        raise BrowserError("page начинается с 1")
    parsed_region = _region(region)
    store = cursors if cursors is not None else {}
    limit = deadline or Deadline(None)
    common = {
        "query": text,
        "page": number,
        "region": parsed_region,
        "safe": safe_search,
        "time_range": time_range,
        "custom": custom,
        "cursors": store,
        "deadline": limit,
    }
    outcome = SearchOutcome(engine=engine, query=text, page=number)
    if engine != "all":
        parsed, notes, documents = _run_one(fetch, engine, **common)
        outcome.results = parsed.results
        outcome.ads_skipped = parsed.ads
        outcome.has_next = parsed.has_next
        outcome.notes = notes
        outcome.documents = documents
        logger.debug("search %s page %d: %d results", engine, number, len(parsed.results))
        return outcome
    finished: list[tuple[str, _Parsed]] = []
    failures: list[tuple[str, BrowserError]] = []
    for name in ("duckduckgo", "bing"):
        try:
            parsed, notes, documents = _run_one(fetch, name, **common)
        except BrowserError as exc:
            failures.append((name, exc))
            continue
        finished.append((name, parsed))
        outcome.notes.extend(notes)
        outcome.documents.extend(documents)
    if not finished:
        raise failures[0][1]
    for name, exc in failures:
        other = ", ".join(done for done, _ in finished)
        outcome.notes.append(f"{name}: {exc} — результаты только {other}")
    merged: list[SearchResult] = []
    by_key: dict[str, SearchResult] = {}
    longest = max(len(parsed.results) for _, parsed in finished)
    for index in range(longest):
        for _name, parsed in finished:
            if index >= len(parsed.results):
                continue
            item = parsed.results[index]
            key = _normalized(item.url)
            existing = by_key.get(key)
            if existing is not None:
                engines = tuple(dict.fromkeys(existing.engines + item.engines))
                existing.engines = engines
                if not existing.snippet and item.snippet:
                    existing.snippet = item.snippet
                continue
            copy = replace(item, rank=len(merged) + 1)
            by_key[key] = copy
            merged.append(copy)
    outcome.results = merged
    outcome.ads_skipped = sum(parsed.ads for _, parsed in finished)
    outcome.has_next = any(parsed.has_next for _, parsed in finished)
    logger.debug("search all page %d: %d results", number, len(merged))
    return outcome


def _quote_arg(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def render_results(outcome: SearchOutcome) -> str:
    """Numbered results with address and snippet, and how to get the next page."""
    count = len(outcome.results)
    header = (
        f"Поиск «{outcome.query}» ({outcome.engine}, страница {outcome.page}): "
        + (f"найдено {count}" if count else "ничего не найдено")
    )
    lines = [header]
    show_engines = outcome.engine == "all"
    for item in outcome.results:
        lines.append("")
        lines.append(f"{item.rank}. {item.title or item.display_url or display_url(item.url)}")
        lines.append(f"   {display_url(item.url)}")
        engines = f" ({', '.join(item.engines)})" if show_engines else ""
        if item.snippet:
            lines.append(f"   {item.snippet}{engines}")
        elif engines:
            lines.append(f"  {engines}")
    if outcome.has_next:
        lines.append("")
        lines.append(
            f"Следующая страница: browser_search(query='{_quote_arg(outcome.query)}', "
            f"engine='{outcome.engine}', page={outcome.page + 1})"
        )
    return "\n".join(lines)


__all__ = [
    "ENGINES",
    "CustomEngine",
    "SearchOutcome",
    "SearchResult",
    "render_results",
    "search",
]
