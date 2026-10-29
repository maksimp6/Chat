"""Page metadata: title, description, Open Graph, canonical, feeds, JSON-LD, microdata."""

from __future__ import annotations

import json
import logging
import re
from typing import Any
from urllib.parse import urljoin

from browser.dom import Document, Element, normalize_space

logger = logging.getLogger("logos.browser.metadata")

_META_NAMES = {
    "description": "description",
    "keywords": "keywords",
    "author": "author",
    "robots": "robots",
    "viewport": "viewport",
    "generator": "generator",
}
_PUBLISHED_NAMES = (
    "article:published_time",
    "og:published_time",
    "date",
    "pubdate",
    "publish-date",
    "publishdate",
    "dc.date",
    "dc.date.issued",
    "dcterms.created",
    "dcterms.issued",
    "citation_publication_date",
    "sailthru.date",
)
_MODIFIED_NAMES = (
    "article:modified_time",
    "og:updated_time",
    "dc.date.modified",
    "dcterms.modified",
    "last-modified",
)
_FEED_TYPES = frozenset(
    {
        "application/rss+xml",
        "application/atom+xml",
        "application/feed+json",
        "application/json+feed",
        "application/rdf+xml",
        "application/xml",
        "text/xml",
    }
)
_OG_URL_KEYS = frozenset(
    {"url", "image", "image:url", "image:secure_url", "video", "video:url", "audio"}
)
_REFERRER_POLICIES = frozenset(
    {
        "no-referrer",
        "no-referrer-when-downgrade",
        "same-origin",
        "origin",
        "strict-origin",
        "origin-when-cross-origin",
        "strict-origin-when-cross-origin",
        "unsafe-url",
    }
)
_LEGACY_POLICIES = {
    "never": "no-referrer",
    "default": "strict-origin-when-cross-origin",
    "always": "unsafe-url",
    "origin-when-crossorigin": "origin-when-cross-origin",
}
_REFRESH = re.compile(r"^\s*(\d+(?:\.\d*)?)?\s*[;,]?\s*(?:url\s*=\s*)?(.*)$", re.I | re.S)
_TRAILING_COMMA = re.compile(r",(\s*[}\]])")
# Microdata: the property value comes from an attribute on these elements.
_URL_VALUE_ATTRS = {
    "audio": "src",
    "embed": "src",
    "iframe": "src",
    "img": "src",
    "source": "src",
    "track": "src",
    "video": "src",
    "a": "href",
    "area": "href",
    "link": "href",
    "object": "data",
}


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


def _rels(element: Element) -> list[str]:
    return (element.attrs.get("rel") or "").lower().split()


def _add_property(target: dict[str, Any], key: str, value: Any) -> None:
    if key not in target:
        target[key] = value
        return
    current = target[key]
    if isinstance(current, list):
        current.append(value)
    else:
        target[key] = [current, value]


def referrer_policy(doc: Document) -> str | None:
    """The policy of <meta name="referrer">, the last valid token winning."""
    found: str | None = None
    for element in doc.elements:
        if element.tag != "meta" or (element.attrs.get("name") or "").strip().lower() != "referrer":
            continue
        for token in re.split(r"[\s,]+", (element.attrs.get("content") or "").lower()):
            token = _LEGACY_POLICIES.get(token, token)
            if token in _REFERRER_POLICIES:
                found = token
    return found


def _parse_refresh(content: str, base_url: str) -> dict[str, Any] | None:
    match = _REFRESH.match(content or "")
    if match is None:
        return None
    delay_text, target = match.group(1), match.group(2).strip().strip("'\"")
    try:
        delay: float | int = float(delay_text) if delay_text else 0
    except ValueError:
        delay = 0
    if isinstance(delay, float) and delay.is_integer():
        delay = int(delay)
    return {"delay": delay, "url": _absolute(base_url, target) if target else None}


def page_metadata(doc: Document, base_url: str) -> dict[str, Any]:
    """Metadata of an HTML document; every URL absolute against ``base_url``."""
    root = doc.html
    result: dict[str, Any] = {
        "title": doc.title,
        "lang": (root.attrs.get("lang") or "").strip() if root is not None else "",
        "dir": (root.attrs.get("dir") or "").strip() if root is not None else "",
        "charset_meta": None,
        "description": None,
        "keywords": None,
        "author": None,
        "robots": None,
        "viewport": None,
        "generator": None,
        "referrer_policy": referrer_policy(doc),
        "canonical": None,
        "base_href": _absolute(base_url, doc.base_href),
        "og": {},
        "twitter": {},
        "article": {},
        "published": None,
        "modified": None,
        "alternates": [],
        "feeds": [],
        "icons": [],
        "manifest": None,
        "amphtml": None,
        "prev": None,
        "next": None,
        "refresh": None,
    }
    named: dict[str, str] = {}
    for element in doc.elements:
        attrs = element.attrs
        if element.tag == "meta":
            content = attrs.get("content")
            if "charset" in attrs and result["charset_meta"] is None:
                result["charset_meta"] = attrs["charset"].strip() or None
            equiv = (attrs.get("http-equiv") or "").strip().lower()
            if equiv == "content-type" and content and result["charset_meta"] is None:
                found = re.search(r"charset\s*=\s*[\"']?([\w.:-]+)", content, re.I)
                if found:
                    result["charset_meta"] = found.group(1)
            if equiv == "refresh" and content is not None and result["refresh"] is None:
                result["refresh"] = _parse_refresh(content, base_url)
            if content is None:
                continue
            value = normalize_space(content)
            for key in ("property", "name", "itemprop"):
                name = (attrs.get(key) or "").strip().lower()
                if not name:
                    continue
                named.setdefault(name, value)
                for prefix, bucket in (("og:", "og"), ("twitter:", "twitter"), ("article:", "article")):
                    if name.startswith(prefix):
                        sub = name[len(prefix) :]
                        stored = (
                            _absolute(base_url, value) or value
                            if bucket != "article" and sub in _OG_URL_KEYS
                            else value
                        )
                        _add_property(result[bucket], sub, stored)
                break
        elif element.tag in ("link", "a") and "href" in attrs:
            rels = _rels(element)
            if not rels:
                continue
            href = _absolute(base_url, attrs.get("href"))
            if href is None:
                continue
            kind = (attrs.get("type") or "").strip().lower()
            title = normalize_space(attrs.get("title"))
            if element.tag == "link":
                if "canonical" in rels and result["canonical"] is None:
                    result["canonical"] = href
                if "alternate" in rels or "feed" in rels:
                    if kind in _FEED_TYPES or "feed" in rels:
                        result["feeds"].append({"type": kind or "feed", "title": title, "href": href})
                    else:
                        result["alternates"].append(
                            {
                                "hreflang": (attrs.get("hreflang") or "").strip() or None,
                                "media": (attrs.get("media") or "").strip() or None,
                                "type": kind or None,
                                "href": href,
                                "title": title or None,
                            }
                        )
                if any("icon" in rel for rel in rels):
                    result["icons"].append(
                        {
                            "rel": " ".join(rels),
                            "href": href,
                            "sizes": (attrs.get("sizes") or "").strip() or None,
                        }
                    )
                if "manifest" in rels and result["manifest"] is None:
                    result["manifest"] = href
                if "amphtml" in rels and result["amphtml"] is None:
                    result["amphtml"] = href
            if "prev" in rels or "previous" in rels:
                if result["prev"] is None:
                    result["prev"] = href
            if "next" in rels and result["next"] is None:
                result["next"] = href
    for key, name in _META_NAMES.items():
        result[key] = named.get(name)
    result["published"] = next((named[name] for name in _PUBLISHED_NAMES if named.get(name)), None)
    result["modified"] = next((named[name] for name in _MODIFIED_NAMES if named.get(name)), None)
    if result["published"] is None:
        for element in doc.elements:
            if element.tag == "time" and (element.attrs.get("itemprop") or "").lower() == "datepublished":
                result["published"] = (element.attrs.get("datetime") or "").strip() or normalize_space(
                    element.text()
                )
                break
    return result


def _strip_wrappers(text: str) -> str:
    value = text.strip()
    for opener, closer in (("<!--", "-->"), ("<![CDATA[", "]]>")):
        if value.startswith(opener):
            value = value[len(opener) :]
            if value.rstrip().endswith(closer):
                value = value.rstrip()[: -len(closer)]
            value = value.strip()
    value = re.sub(r"^\s*//\s*<!\[CDATA\[", "", value)
    value = re.sub(r"//\s*\]\]>\s*$", "", value)
    return value.strip()


def _load_json(text: str) -> Any:
    try:
        return json.loads(text, strict=False)
    except ValueError:
        return json.loads(_TRAILING_COMMA.sub(r"\1", text), strict=False)


def _script_type(element: Element) -> str:
    return (element.attrs.get("type") or "").split(";", 1)[0].strip().lower()


def json_ld(doc: Document) -> list[dict[str, Any]]:
    """Every JSON-LD script: {"index", "data", "error"} (data None on error)."""
    blocks: list[dict[str, Any]] = []
    for element in doc.elements:
        if element.tag != "script" or "ld+json" not in _script_type(element):
            continue
        index = len(blocks) + 1
        text = _strip_wrappers(element.text(skip=()))
        if not text:
            blocks.append({"index": index, "data": None, "error": "пустой блок"})
            continue
        try:
            blocks.append({"index": index, "data": _load_json(text), "error": None})
        except ValueError as exc:
            blocks.append({"index": index, "data": None, "error": str(exc)})
    return blocks


def inline_json_scripts(doc: Document) -> list[dict[str, Any]]:
    """JSON data scripts other than JSON-LD (__NEXT_DATA__ and the like)."""
    found = []
    for element in doc.elements:
        if element.tag != "script":
            continue
        kind = _script_type(element)
        if "json" not in kind or "ld+json" in kind:
            continue
        found.append(
            {
                "id": element.attrs.get("id") or None,
                "type": kind,
                "chars": len(element.text(skip=())),
            }
        )
    return found


def _microdata_value(element: Element, base_url: str) -> str:
    attrs = element.attrs
    tag = element.tag
    if tag == "meta":
        return attrs.get("content", "")
    attribute = _URL_VALUE_ATTRS.get(tag)
    if attribute is not None:
        return _absolute(base_url, attrs.get(attribute)) or ""
    if tag in ("data", "meter") and "value" in attrs:
        return attrs.get("value", "")
    if tag == "time" and "datetime" in attrs:
        return attrs.get("datetime", "")
    if "content" in attrs:
        return attrs.get("content", "")
    return normalize_space(element.text())


def _item_properties(item: Element, doc: Document) -> list[Element]:
    """Elements carrying properties of ``item`` (its subtree and itemref targets)."""
    roots = [item]
    for ref_id in (item.attrs.get("itemref") or "").split():
        target = doc.by_id(ref_id)
        if target is not None:
            roots.append(target)
    found: list[Element] = []
    seen: set[int] = set()
    for root in roots:
        stack = [root] if root is not item else list(reversed(item.element_children()))
        while stack:
            node = stack.pop()
            if node.index in seen:
                continue
            seen.add(node.index)
            if "itemprop" in node.attrs:
                found.append(node)
            if "itemscope" in node.attrs:
                continue
            stack.extend(reversed(node.element_children()))
    found.sort(key=lambda element: element.index)
    return found


def microdata(doc: Document, base_url: str) -> list[dict[str, Any]]:
    """Top-level microdata items as {"@type", "@id", property: value | [values]}."""
    items: list[dict[str, Any]] = []
    # (item element, its dict, the item elements it is nested in): a nested item
    # that is one of its own ancestors is an itemref cycle.
    work: list[tuple[Element, dict[str, Any], frozenset[int]]] = []
    for element in doc.elements:
        if "itemscope" in element.attrs and "itemprop" not in element.attrs:
            item: dict[str, Any] = {}
            items.append(item)
            work.append((element, item, frozenset()))
    while work:
        element, target, outer = work.pop()
        chain = outer | {element.index}
        item_type = normalize_space(element.attrs.get("itemtype"))
        if item_type:
            target["@type"] = item_type
        item_id = element.attrs.get("itemid")
        if item_id:
            target["@id"] = _absolute(base_url, item_id) or item_id
        for prop in _item_properties(element, doc):
            names = (prop.attrs.get("itemprop") or "").split()
            if "itemscope" in prop.attrs:
                if prop.index in chain:
                    value: Any = {"@error": "цикл itemref"}
                else:
                    value = {}
                    work.append((prop, value, chain))
            else:
                value = _microdata_value(prop, base_url)
            for name in names:
                _add_property(target, name, value)
    return items


__all__ = [
    "inline_json_scripts",
    "json_ld",
    "microdata",
    "page_metadata",
    "referrer_policy",
]
