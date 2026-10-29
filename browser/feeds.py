"""RSS, Atom, RDF and JSON Feed parsing over the tolerant XML tree.

Element names are matched by local name, so namespace prefixes do not matter.
``feed_to_html`` renders a feed as a small HTML page whose item links get the
refs L1, L2… in item order.
"""

from __future__ import annotations

import html
import logging
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin

from browser.dom import Document, Element, normalize_space, parse_html, serialize

logger = logging.getLogger("logos.browser.feeds")

_BLOCKS = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "dd",
        "div",
        "dl",
        "dt",
        "figcaption",
        "figure",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "td",
        "th",
        "tr",
        "ul",
    }
)
_SKIP = frozenset({"script", "style", "template", "head"})


@dataclass
class FeedItem:
    title: str = ""
    link: str | None = None
    id: str | None = None
    published: str | None = None
    updated: str | None = None
    author: str | None = None
    summary: str = ""
    categories: list[str] = field(default_factory=list)
    enclosures: list[tuple[str, str | None, int | None]] = field(default_factory=list)


@dataclass
class Feed:
    kind: str  # rss|atom|rdf|json
    title: str = ""
    link: str | None = None
    description: str = ""
    language: str | None = None
    updated: str | None = None
    items: list[FeedItem] = field(default_factory=list)


def _local(tag: str) -> str:
    return tag.rsplit(":", 1)[-1]


def _kids(element: Element | None, name: str) -> list[Element]:
    if element is None:
        return []
    return [kid for kid in element.element_children() if _local(kid.tag) == name]


def _kid(element: Element | None, *names: str) -> Element | None:
    if element is None:
        return None
    for name in names:
        for kid in element.element_children():
            if _local(kid.tag) == name:
                return kid
    return None


def _plain(element: Element | None) -> str:
    return normalize_space(element.text(skip=())) if element is not None else ""


def _optional(element: Element | None) -> str | None:
    return _plain(element) or None


def _block_text(root: Element) -> str:
    """Text of a subtree with block boundaries kept as spaces."""
    parts: list[str] = []
    stack: list[Element | str] = [root]
    while stack:
        node = stack.pop()
        if isinstance(node, str):
            parts.append(node)
            continue
        if node is not root and _local(node.tag) in _SKIP:
            continue
        if _local(node.tag) in _BLOCKS:
            parts.append(" ")
            stack.append(" ")
        stack.extend(reversed(node.children))
    return normalize_space("".join(parts))


def html_to_text(fragment: str) -> str:
    """Readable one-paragraph text of an HTML fragment (feed summaries)."""
    if "<" not in fragment or ">" not in fragment:
        return normalize_space(html.unescape(fragment) if "&" in fragment else fragment)
    return _block_text(parse_html(fragment).root)


def _content_text(element: Element | None) -> str:
    """Text of a feed text construct: markup children, escaped HTML or plain text."""
    if element is None:
        return ""
    if any(isinstance(kid, Element) for kid in element.children):
        return _block_text(element)
    raw = element.text(skip=())
    return html_to_text(raw)


def _absolute(base_url: str, value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return urljoin(base_url, text)
    except ValueError:
        return text


def _int(value: Any) -> int | None:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _rss_link(element: Element, base_url: str) -> str | None:
    links = _kids(element, "link")
    for link in links:
        text = _plain(link)
        if text:
            return _absolute(base_url, text)
    for link in links:
        rel = (link.attrs.get("rel") or "alternate").strip().lower()
        if rel == "alternate" and link.attrs.get("href"):
            return _absolute(base_url, link.attrs["href"])
    return None


def _atom_link(element: Element, base_url: str) -> str | None:
    links = [link for link in _kids(element, "link") if (link.attrs.get("href") or "").strip()]
    for link in links:
        rel = (link.attrs.get("rel") or "alternate").strip().lower()
        kind = (link.attrs.get("type") or "").strip().lower()
        if rel == "alternate" and (not kind or "html" in kind):
            return _absolute(base_url, link.attrs["href"])
    for link in links:
        if (link.attrs.get("rel") or "alternate").strip().lower() == "alternate":
            return _absolute(base_url, link.attrs["href"])
    return _absolute(base_url, links[0].attrs["href"]) if links else None


def _media_enclosures(element: Element, base_url: str) -> list[tuple[str, str | None, int | None]]:
    found: list[tuple[str, str | None, int | None]] = []
    holders = [element] + _kids(element, "group")
    for holder in holders:
        for kid in holder.element_children():
            name = _local(kid.tag)
            if name == "enclosure":
                url = _absolute(base_url, kid.attrs.get("url"))
                length = _int(kid.attrs.get("length"))
            elif name == "content" and kid.attrs.get("url"):
                url = _absolute(base_url, kid.attrs.get("url"))
                length = _int(kid.attrs.get("filesize"))
            else:
                continue
            if url:
                found.append((url, (kid.attrs.get("type") or "").strip() or None, length))
    return found


def _rss_item(item: Element, base_url: str) -> FeedItem:
    guid = _kid(item, "guid")
    guid_text = _plain(guid) or None
    link = _rss_link(item, base_url)
    if link is None and guid_text and guid is not None:
        permalink = (guid.attrs.get("ispermalink") or "true").strip().lower() != "false"
        if permalink and guid_text.startswith(("http://", "https://")):
            link = guid_text
    summary_element = _kid(item, "encoded") or _kid(item, "description", "summary")
    categories = [_plain(kid) for kid in _kids(item, "category") + _kids(item, "subject")]
    return FeedItem(
        title=_content_text(_kid(item, "title")),
        link=link,
        id=guid_text,
        published=_optional(_kid(item, "pubdate", "date", "issued", "published")),
        updated=_optional(_kid(item, "updated", "modified")),
        author=_optional(_kid(item, "creator", "author")),
        summary=_content_text(summary_element),
        categories=[category for category in categories if category],
        enclosures=_media_enclosures(item, base_url),
    )


def _atom_entry(entry: Element, base_url: str) -> FeedItem:
    author = _kid(entry, "author")
    author_name = _plain(_kid(author, "name")) if author is not None else ""
    categories = []
    for category in _kids(entry, "category"):
        label = normalize_space(category.attrs.get("label") or category.attrs.get("term"))
        if label:
            categories.append(label)
    enclosures: list[tuple[str, str | None, int | None]] = []
    for link in _kids(entry, "link"):
        if (link.attrs.get("rel") or "").strip().lower() == "enclosure":
            url = _absolute(base_url, link.attrs.get("href"))
            if url:
                enclosures.append(
                    (url, (link.attrs.get("type") or "").strip() or None, _int(link.attrs.get("length")))
                )
    enclosures.extend(_media_enclosures(entry, base_url))
    # media:content shares the local name "content"; the text construct has no url.
    content = next(
        (kid for kid in _kids(entry, "content") if "url" not in kid.attrs), None
    )
    return FeedItem(
        title=_content_text(_kid(entry, "title")),
        link=_atom_link(entry, base_url),
        id=_optional(_kid(entry, "id")),
        published=_optional(_kid(entry, "published", "issued")),
        updated=_optional(_kid(entry, "updated", "modified")),
        author=author_name or (_plain(author) if author is not None else None) or None,
        summary=_content_text(content if content is not None else _kid(entry, "summary")),
        categories=categories,
        enclosures=enclosures,
    )


def _top(doc: Document) -> Element | None:
    return next(iter(doc.root.element_children()), None)


def detect_feed(doc: Document) -> str | None:
    """"rss", "atom" or "rdf" by the document element's local name, else None."""
    top = _top(doc)
    if top is None:
        return None
    name = _local(top.tag)
    if name == "rss":
        return "rss"
    if name == "feed":
        return "atom"
    if name == "rdf":
        return "rdf"
    return None


def parse_feed(doc: Document, base_url: str) -> Feed | None:
    """Parse an RSS, Atom or RDF document; None when it is not a feed."""
    kind = detect_feed(doc)
    top = _top(doc)
    if kind is None or top is None:
        return None
    if kind == "atom":
        feed = Feed(
            kind="atom",
            title=_content_text(_kid(top, "title")),
            link=_atom_link(top, base_url),
            description=_content_text(_kid(top, "subtitle", "tagline")),
            language=(top.attrs.get("xml:lang") or "").strip() or None,
            updated=_optional(_kid(top, "updated", "modified")),
        )
        feed.items = [_atom_entry(entry, base_url) for entry in _kids(top, "entry")]
        return feed
    channel = _kid(top, "channel")
    holder = channel if channel is not None else top
    feed = Feed(
        kind=kind,
        title=_content_text(_kid(holder, "title")),
        link=_rss_link(holder, base_url),
        description=_content_text(_kid(holder, "description")),
        language=_optional(_kid(holder, "language")),
        updated=_optional(_kid(holder, "lastbuilddate", "pubdate", "date", "updated")),
    )
    items = _kids(holder, "item") if kind == "rss" else []
    if not items:
        items = _kids(top, "item")
    feed.items = [_rss_item(item, base_url) for item in items]
    return feed


def _json_text(value: Any) -> str:
    return normalize_space(value) if isinstance(value, str) else ""


def parse_json_feed(obj: Any, base_url: str) -> Feed | None:
    """Parse a JSON Feed (jsonfeed.org); None when ``obj`` is not one."""
    if not isinstance(obj, dict):
        return None
    if "jsonfeed" not in str(obj.get("version") or "").lower():
        return None
    feed = Feed(
        kind="json",
        title=_json_text(obj.get("title")),
        link=_absolute(base_url, obj.get("home_page_url") if isinstance(obj.get("home_page_url"), str) else None),
        description=_json_text(obj.get("description")),
        language=_json_text(obj.get("language")) or None,
    )
    raw_items = obj.get("items")
    for raw in raw_items if isinstance(raw_items, list) else []:
        if not isinstance(raw, dict):
            continue
        author = None
        authors = raw.get("authors")
        if isinstance(authors, list) and authors and isinstance(authors[0], dict):
            author = _json_text(authors[0].get("name")) or None
        elif isinstance(raw.get("author"), dict):
            author = _json_text(raw["author"].get("name")) or None
        if isinstance(raw.get("content_html"), str) and raw["content_html"].strip():
            summary = html_to_text(raw["content_html"])
        elif isinstance(raw.get("content_text"), str) and raw["content_text"].strip():
            summary = normalize_space(raw["content_text"])
        else:
            summary = _json_text(raw.get("summary"))
        link_value = raw.get("url") if isinstance(raw.get("url"), str) else raw.get("external_url")
        enclosures: list[tuple[str, str | None, int | None]] = []
        attachments = raw.get("attachments")
        for attachment in attachments if isinstance(attachments, list) else []:
            if isinstance(attachment, dict) and isinstance(attachment.get("url"), str):
                url = _absolute(base_url, attachment["url"])
                if url:
                    mime = attachment.get("mime_type")
                    enclosures.append(
                        (url, mime if isinstance(mime, str) else None, _int(attachment.get("size_in_bytes")))
                    )
        tags = raw.get("tags")
        feed.items.append(
            FeedItem(
                title=_json_text(raw.get("title")),
                link=_absolute(base_url, link_value) if isinstance(link_value, str) else None,
                id=str(raw["id"]) if raw.get("id") is not None else None,
                published=_json_text(raw.get("date_published")) or None,
                updated=_json_text(raw.get("date_modified")) or None,
                author=author,
                summary=summary,
                categories=[_json_text(tag) for tag in tags if _json_text(tag)]
                if isinstance(tags, list)
                else [],
                enclosures=enclosures,
            )
        )
    return feed


def _enclosure_text(enclosure: tuple[str, str | None, int | None]) -> str:
    url, kind, length = enclosure
    details = ", ".join(part for part in (kind, f"{length} байт" if length is not None else None) if part)
    return f"{url} ({details})" if details else url


def feed_to_html(feed: Feed) -> str:
    """A synthetic HTML page of the feed: item links become L1, L2… in item order."""
    escape = html.escape
    kinds = {"rss": "RSS", "atom": "Atom", "rdf": "RDF", "json": "JSON Feed"}
    parts = [
        "<!DOCTYPE html><html><head><title>",
        escape(feed.title or "Лента"),
        "</title></head><body>",
        f"<h1>{escape(feed.title or 'Лента')}</h1>",
    ]
    facts = [f"Лента {kinds.get(feed.kind, feed.kind)}", f"записей: {len(feed.items)}"]
    if feed.updated:
        facts.append(f"обновлена {feed.updated}")
    if feed.language:
        facts.append(f"язык {feed.language}")
    parts.append(f"<p>{escape(', '.join(facts))}</p>")
    if feed.description:
        parts.append(f"<p>{escape(feed.description)}</p>")
    for item in feed.items:
        title = escape(item.title or item.link or item.id or "(без заголовка)")
        parts.append("<article>")
        if item.link:
            parts.append(f'<h2><a href="{escape(item.link, quote=True)}">{title}</a></h2>')
        else:
            parts.append(f"<h2>{title}</h2>")
        facts = [part for part in (item.published or item.updated, item.author) if part]
        if facts:
            parts.append(f"<p>{escape(' · '.join(facts))}</p>")
        if item.summary:
            parts.append(f"<p>{escape(item.summary)}</p>")
        if item.categories:
            parts.append(f"<p>Категории: {escape(', '.join(item.categories))}</p>")
        for enclosure in item.enclosures:
            parts.append(f"<p>Вложение: {escape(_enclosure_text(enclosure))}</p>")
        parts.append("</article>")
    if feed.link:
        parts.append(f'<p>Сайт: <a href="{escape(feed.link, quote=True)}">{escape(feed.link)}</a></p>')
    parts.append("</body></html>")
    return "".join(parts)


def pretty_xml(doc: Document) -> str:
    """The XML document pretty-printed, one element per line."""
    return serialize(doc, indent=True)


__all__ = [
    "Feed",
    "FeedItem",
    "detect_feed",
    "feed_to_html",
    "html_to_text",
    "parse_feed",
    "parse_json_feed",
    "pretty_xml",
]
