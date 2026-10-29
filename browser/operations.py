"""One operation per browser tool: argument handling, engines, results.

Every operation is ``op(session, args, ctx) -> dict``: it validates its
arguments (missing and None are the same), drives the tab engines through the
session, and returns the result data; ``browser/tools.py`` adds
``"success": True`` and turns a ``BrowserError`` into a failed result. Long
text is returned in windows (``max_chars``, ``offset``, ``next_offset``);
nothing is cut silently, and every skipped part is reported in ``notes``.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import re
import time
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Callable, Mapping
from urllib.parse import unquote, urlencode, urlsplit

from browser import css, forms, views
from browser import files as files_mod
from browser import search as search_mod
from browser.charsets import GENERIC_MIMES, classify, decode_body
from browser.dom import Element
from browser.engine import (
    Action,
    ActionOutcome,
    CaptureRequest,
    ElementTarget,
    Navigation,
    NavOutcome,
    WaitRequest,
)
from browser.errors import (
    E_ARG_NEGATIVE,
    E_ARG_TYPE,
    E_BAD_REQUEST,
    E_BINARY_VIEW,
    E_BLOB_LINK,
    E_BODY_CONFLICT,
    E_BODY_CUT,
    E_CAPTURE_FORMAT,
    E_COOKIE_FIELDS,
    E_EVAL_REQUIRED,
    E_FILE_MISSING,
    E_FORM_MIXED,
    E_HISTORY_FWD,
    E_JSON_BODY,
    E_NOT_JSON,
    E_OPTION_NOT_FOUND,
    E_PRESS_NO_FORM,
    E_REQUEST_URL,
    E_RESPONSE_ID,
    E_RESULT_ID,
    E_SELECTOR_NONE,
    E_SESSION_NOT_FOUND,
    E_TAB_EMPTY,
    E_TAB_LOST,
    E_TARGET_REQUIRED,
    E_URL_SCHEME,
    E_VIEW_PARAM,
    BrowserError,
    BrowserNotFound,
    BrowserUnavailable,
    Deadline,
    auto_no_chrome_message,
    chrome_missing_message,
    chrome_only_message,
    history_back_message,
)
from browser.files import AtomicWriter, disk_free, extension_for, resolve_target
from browser.http_engine import (
    RequestSpec,
    cookie_rows,
    clear_cookies,
    decode_data_url,
    delete_cookies,
    encode_multipart,
    hop_lines,
    jar_to_cdp,
    referrer_value,
    replay_spec,
    set_cookie,
    substitute_cookie_placeholders,
)
from browser.http_tab import HttpTabEngine
from browser.page import MATCH_ATTR, REF_COUNT_NAMES, REF_PREFIX_ORDER
from browser.paging import (
    arg_bool,
    arg_float,
    arg_int,
    arg_list,
    arg_pairs,
    arg_raw_str,
    arg_str,
    human_bytes,
    normalize_ref,
    paginate,
    parse_ref,
)
from browser.redact import (
    bearer_credential,
    display_url,
    is_sensitive_name,
    mask_headers,
    userinfo_password,
)
from browser.sessions import (
    BrowserSession,
    StoredResponse,
    Tab,
    drop_session,
    find_session,
    get_session,
    list_sessions,
)
from browser.settings import DIALOG_MODES, ENGINE_MODES

logger = logging.getLogger("logos.browser.operations")

_WAIT_UNTIL = ("commit", "domcontentloaded", "load", "networkidle", "networkidle2")
_OPEN_SCHEMES = ("http", "https", "file", "data")
_KNOWN_SCHEMES = frozenset(
    {
        "http", "https", "file", "data", "about", "javascript", "mailto", "tel", "ftp",
        "ws", "wss", "blob", "chrome", "view-source", "sms", "geo", "magnet", "intent",
    }
)
_CHALLENGE_TITLES = frozenset(
    {
        "just a moment...",
        "just a moment…",
        "один момент…",
        "один момент...",
        "attention required! | cloudflare",
        "ddos-guard",
        "checking your browser before accessing",
    }
)
_REOPENED_NOTE = "Страница открыта в chrome, метки обновились — повторите действие с новой меткой"
_CAPTURE_MIMES = {"png": "image/png", "jpeg": "image/jpeg", "pdf": "application/pdf"}
_CAPTURE_EXTENSIONS = {".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg", ".pdf": "pdf"}
_MARKDOWN_HEADING = re.compile(r"^(#{1,6}) (.+?)\s*$")


# ---------------------------------------------------------------------------
# Argument helpers


def _non_negative(args: Mapping[str, Any], name: str) -> int:
    value = arg_int(args, name)
    if value is None:
        return 0
    if value < 0:
        raise BrowserError(E_ARG_NEGATIVE.format(name=name, value=value))
    return value


def _optional_offset(args: Mapping[str, Any]) -> int | None:
    value = arg_int(args, "offset")
    if value is not None and value < 0:
        raise BrowserError(E_ARG_NEGATIVE.format(name="offset", value=value))
    return value


def _choice(
    args: Mapping[str, Any], name: str, allowed: tuple[str, ...] | list[str], default: str | None = None
) -> str | None:
    value = arg_str(args, name)
    if value is None:
        return default
    if value in allowed:
        return value
    lowered = value.lower()
    for item in allowed:
        if item.lower() == lowered:
            return item
    raise BrowserError(
        E_ARG_TYPE.format(name=name, expected="одним из: " + ", ".join(allowed), value=value)
    )


def _deadline(args: Mapping[str, Any]) -> Deadline:
    seconds = arg_float(args, "timeout_seconds")
    if seconds is not None and seconds < 0:
        raise BrowserError(E_ARG_NEGATIVE.format(name="timeout_seconds", value=seconds))
    return Deadline(seconds)


def _target(args: Mapping[str, Any]) -> ElementTarget:
    ref = arg_str(args, "ref")
    nth = arg_int(args, "nth")
    if nth is not None and nth < 1:
        raise BrowserError(E_ARG_TYPE.format(name="nth", expected="числом от 1", value=nth))
    return ElementTarget(
        ref=normalize_ref(ref) if ref else None,
        selector=arg_str(args, "selector"),
        text=arg_str(args, "text"),
        x=arg_float(args, "x"),
        y=arg_float(args, "y"),
        nth=nth,
    )


def _secret_header(ctx: Any, name: str, value: str | None) -> None:
    if value and is_sensitive_name(name):
        ctx.secret(value)
        credential = bearer_credential(value)
        if credential:
            ctx.secret(credential)


def _merge_notes(*groups: Any) -> list[str]:
    merged: list[str] = []
    for group in groups:
        for note in group or []:
            text = str(note).strip()
            if text and text not in merged:
                merged.append(text)
    return merged


def _is_http_url(url: str | None) -> bool:
    try:
        return (urlsplit(url or "").scheme or "").lower() in ("http", "https")
    except ValueError:
        return False


def _host(url: str | None) -> str:
    try:
        return (urlsplit(url or "").hostname or "").lower()
    except ValueError:
        return ""


def _iso_date(timestamp: float | int | None) -> str:
    if timestamp is None:
        return ""
    try:
        return datetime.fromtimestamp(float(timestamp), timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except (OverflowError, OSError, ValueError):
        return str(timestamp)


# ---------------------------------------------------------------------------
# Pages and results


def _ref_counts(page: views.ViewPage) -> dict[str, int]:
    try:
        counts = page.ref_counts() or {}
    except Exception:
        counts = {}
    result = {name: 0 for name in REF_COUNT_NAMES.values()}
    for key, value in counts.items():
        name = REF_COUNT_NAMES.get(key, key)
        if name in result:
            result[name] = int(value or 0)
    return result


def _binary_error(tab: Tab, page: views.ViewPage) -> BrowserError:
    snapshot = page.snapshot
    info = snapshot.binary
    return BrowserError(
        E_BINARY_VIEW.format(
            tab=tab.id,
            mime=(info.mime if info else snapshot.mime) or "application/octet-stream",
            size=human_bytes(info.length if info and info.length is not None else snapshot.bytes),
        )
    )


def _check_lost(tab: Tab) -> None:
    """E_TAB_LOST for a tab whose Chrome died (without taking a page snapshot)."""
    engine = tab.engine
    if engine is None:
        raise BrowserError(E_TAB_EMPTY.format(tab=tab.id))
    if tab.lost_reason or getattr(engine, "lost", False):
        raise BrowserError(
            E_TAB_LOST.format(
                tab=tab.id,
                reason=tab.lost_reason or "Chrome завершился",
                url=display_url(tab.last_url) or "…",
            )
        )


def current_page(session: BrowserSession, tab: Tab) -> views.ViewPage:
    """The tab's current page; a tab whose Chrome died raises E_TAB_LOST."""
    engine = tab.engine
    if engine is not None and engine.name == "chrome" and tab.lost_reason is None:
        session.chrome_if_running()
    _check_lost(tab)
    assert engine is not None
    return engine.page()


def page_result(
    session: BrowserSession,
    tab: Tab,
    page: views.ViewPage,
    *,
    view: str,
    max_chars: int | None,
    offset: int | None = None,
    navigated: bool,
    notes: list[str] | None = None,
    extra: Mapping[str, Any] | None = None,
    render_kwargs: Mapping[str, Any] | None = None,
    text: str | None = None,
) -> dict[str, Any]:
    """The common page result: summary, refs, and one window of the requested view."""
    snapshot = page.snapshot
    tab.last_url = page.url
    tab.last_title = page.title
    result: dict[str, Any] = {
        "session": session.key,
        "tab": tab.id,
        "engine": snapshot.engine,
        "mode": tab.mode,
        "url": page.display_url,
        "title": page.title,
        "status": snapshot.status,
        "reason": snapshot.reason,
        "content_type": snapshot.content_type,
        "kind": page.kind,
        "charset": snapshot.charset,
        "bytes": snapshot.bytes,
        "navigated": bool(navigated),
        "refs": _ref_counts(page),
    }
    if navigated and snapshot.headers is not None and snapshot.headers.redirects:
        result["redirects"] = hop_lines(snapshot.headers.redirects, snapshot.url)
    all_notes = _merge_notes(notes, snapshot.notes)
    if text is not None or view != "none":
        if text is None:
            text, view_notes = views.render_view(page, view, **dict(render_kwargs or {}))
            all_notes = _merge_notes(all_notes, view_notes)
        result["view"] = view
        result.update(paginate(text, offset, max_chars))
    if extra:
        result.update(extra)
    if all_notes:
        result["notes"] = all_notes
    return result


def view_scope(
    session: BrowserSession,
    tab: Tab,
    page: views.ViewPage,
    selector: str | None,
    ref: str | None,
    *,
    require: bool = True,
) -> tuple[views.ViewPage, list[Element] | None]:
    """The elements a selector or ref names (Chrome marks selector matches in the live page)."""
    if not selector and not ref:
        return page, None
    if page.model is None:
        raise _binary_error(tab, page)
    if selector:
        engine = tab.engine
        if engine is not None and engine.name == "chrome":
            page = engine.mark_matches(selector)
            model = page.model
            found = [el for el in model.document.elements if MATCH_ATTR in el.attrs] if model else []
        else:
            found = css.select(page.model.document, selector)
        if not found and require:
            raise BrowserNotFound(E_SELECTOR_NONE.format(sel=selector))
        return page, found
    return page, [page.model.element(normalize_ref(ref))]


def _apply(
    session: BrowserSession, tab: Tab, outcome: Any, ctx: Any
) -> tuple[list[str], dict[str, Any]]:
    notes: list[str] = []
    extra: dict[str, Any] = {}
    popups = list(getattr(outcome, "popups", None) or [])
    if popups:
        ids = session.adopt_popups(popups)
        if ids:
            extra["popups"] = ids
            notes.append(f"Страница открыла новую вкладку: {', '.join(ids)} (теперь текущая {ids[-1]})")
    downloads = session.record_downloads(list(getattr(outcome, "downloads", None) or []))
    downloads.extend(session.drain_chrome(ctx))
    if downloads:
        extra["downloads"] = [
            {
                "name": item.get("name"),
                "path": item.get("path"),
                "bytes": item.get("bytes"),
                "state": item.get("state"),
            }
            for item in downloads
        ]
        for item in downloads:
            notes.append(f"Chrome скачал «{item.get('name')}» → {item.get('path')}")
    for message in getattr(outcome, "dialogs", None) or []:
        notes.append(str(message))
    blocked = list(getattr(outcome, "blocked", None) or [])
    if blocked:
        hosts: dict[str, int] = {}
        for url in blocked:
            host = _host(url) or str(url)
            hosts[host] = hosts.get(host, 0) + 1
        for host, count in hosts.items():
            ctx.event(
                "browser_blocked",
                {"tab": tab.id, "host": host, "stage": "subresource", "count": count},
            )
        notes.append(
            "Заблокированы запросы страницы к адресам link-local: "
            + ", ".join(f"{host} ×{count}" for host, count in hosts.items())
        )
    navigation = getattr(outcome, "new_tab_navigation", None)
    if navigation is not None:
        new_tab = session.new_tab(mode=tab.mode, opener=tab.id)
        nav_outcome, nav_notes = _navigate(session, new_tab, navigation, ctx, action="browser_click")
        more_notes, more_extra = _apply(session, new_tab, nav_outcome, ctx)
        notes.append(f"Открыто в новой вкладке {new_tab.id}")
        notes.extend(nav_notes)
        notes.extend(nav_outcome.notes)
        notes.extend(more_notes)
        extra.update(more_extra)
        extra["opened_tab"] = new_tab.id
    return notes, extra


def apply_outcome(session: BrowserSession, tab: Tab, outcome: Any, ctx: Any) -> list[str]:
    """Adopt popups, record downloads, dialogs and blocked requests, open new-tab navigations."""
    return _apply(session, tab, outcome, ctx)[0]


def challenge_reason(page: views.ViewPage) -> str | None:
    """Why an http page looks like a bot check that only a real browser passes."""
    snapshot = page.snapshot
    headers = snapshot.headers.response_headers if snapshot.headers is not None else []
    mitigated = next((value for name, value in headers if name.lower() == "cf-mitigated"), "")
    if snapshot.status in (403, 503) and mitigated.strip().lower() == "challenge":
        return "проверка Cloudflare (cf-mitigated: challenge)"
    title = (page.title or "").strip()
    if title.lower() in _CHALLENGE_TITLES:
        return f"страница проверки браузера «{title}»"
    return None


def _chrome_status() -> tuple[Any, str | None]:
    from browser import chrome_launch

    return chrome_launch.chrome_status()


def _ensure_live(session: BrowserSession, tab: Tab, *, action: str, deadline: Deadline, ctx: Any) -> None:
    """For a chrome tab: detect a dead Chrome and move the cookie store to Chrome."""
    engine = tab.engine
    if engine is None or engine.name != "chrome" or tab.lost_reason:
        return
    if session.chrome_if_running(ctx) is None:
        return
    session.chrome(action, deadline=deadline, ctx=ctx)


def _navigate(
    session: BrowserSession,
    tab: Tab,
    nav: Navigation,
    ctx: Any,
    *,
    action: str = "browser_open",
) -> tuple[NavOutcome, list[str]]:
    """Load ``nav`` in ``tab`` by the tab's mode (auto: http first, chrome when the page needs it)."""
    notes: list[str] = []
    mode = tab.mode
    http_only = (
        nav.submission is not None
        or nav.body is not None
        or (nav.method or "GET").upper() != "GET"
        or not _is_http_url(nav.url)
    )
    deadline = nav.deadline
    if mode == "chrome" and not http_only:
        engine = session.engine_for(
            tab, "chrome", deadline=deadline, ctx=ctx, action=action, reason="режим вкладки chrome"
        )
        return engine.load(nav, ctx), notes
    if mode == "chrome":
        notes.append("Этот переход выполняет движок http (адрес file:, data: или отправка формы без chrome)")
    current = tab.engine
    if (
        mode == "auto"
        and not http_only
        and current is not None
        and current.name == "chrome"
        and not getattr(current, "lost", False)
        and tab.lost_reason is None
        and tab.last_url
        and _host(tab.last_url) == _host(nav.url)
        and session.chrome_if_running() is not None
    ):
        engine = session.engine_for(
            tab, "chrome", deadline=deadline, ctx=ctx, action=action, reason="тот же сайт уже открыт в chrome"
        )
        return engine.load(nav, ctx), notes
    engine = session.engine_for(tab, "http", deadline=deadline, ctx=ctx, action=action, reason=f"режим вкладки {mode}")
    outcome = engine.load(nav, ctx)
    if mode != "auto" or http_only:
        return outcome, notes
    page = outcome.page
    reason = None
    if page.kind == "html":
        model = page.model
        if model is not None:
            reason = model.script_rendered_reason()
        reason = reason or challenge_reason(page)
    if not reason:
        return outcome, notes
    binary, problem = _chrome_status()
    if binary is None:
        notes.append(auto_no_chrome_message(reason, problem))
        return outcome, notes
    try:
        chrome_engine = session.make_engine(tab, "chrome", deadline=deadline, ctx=ctx, action=action)
    except BrowserError as exc:
        notes.append(f"Не удалось открыть в chrome: {exc}")
        return outcome, notes
    try:
        chrome_outcome = chrome_engine.load(nav, ctx)
    except BrowserError as exc:
        try:
            chrome_engine.close()
        except Exception:
            pass
        notes.append(f"Не удалось открыть в chrome: {exc}")
        return outcome, notes
    session.attach_engine(tab, chrome_engine, ctx=ctx, reason=reason)
    notes.append(f"Страница собирается JavaScript ({reason}); открыта в chrome")
    return chrome_outcome, notes


def chrome_only_guard(
    session: BrowserSession,
    tab: Tab,
    action: str,
    ctx: Any,
    deadline: Deadline,
    *,
    max_chars: int | None = None,
) -> dict[str, Any]:
    """An action that needs Chrome on an http tab: reopen the page in chrome (auto) or explain.

    The action itself is not repeated: refs differ between the engines, so the
    result is the reopened page and the model repeats the action with a new ref.
    """
    page = current_page(session, tab)
    display = page.display_url
    if tab.mode == "http":
        raise BrowserError(chrome_only_message(action, tab.id, display))
    binary, problem = _chrome_status()
    if binary is None:
        raise BrowserUnavailable(chrome_missing_message(action, problem))
    if (page.snapshot.method or "GET").upper() != "GET" or not _is_http_url(page.url):
        raise BrowserError(chrome_only_message(action, tab.id, display))
    navigation = Navigation(url=page.url, initiator="reload", deadline=deadline)
    chrome_engine = session.make_engine(tab, "chrome", deadline=deadline, ctx=ctx, action=action)
    try:
        outcome = chrome_engine.load(navigation, ctx)
    except BaseException:
        try:
            chrome_engine.close()
        except Exception:
            pass
        raise
    session.attach_engine(tab, chrome_engine, ctx=ctx, reason=f"«{action}» выполняется только в chrome")
    more_notes, extra = _apply(session, tab, outcome, ctx)
    extra["reopened_in_chrome"] = True
    view = "content" if max_chars is not None else "none"
    return page_result(
        session,
        tab,
        outcome.page,
        view=view,
        max_chars=max_chars or 0,
        navigated=True,
        notes=_merge_notes([_REOPENED_NOTE], outcome.notes, more_notes),
        extra=extra,
    )


def _needs_chrome(exc: BrowserError) -> bool:
    return bool((getattr(exc, "details", None) or {}).get("needs_chrome"))


def _perform(
    session: BrowserSession,
    tab: Tab,
    action: Action,
    ctx: Any,
    *,
    label: str,
    max_chars: int | None,
) -> tuple[ActionOutcome | None, dict[str, Any] | None]:
    """Run an action; an http-only limit in auto mode reopens the page in chrome instead."""
    engine = tab.engine
    if engine is None:
        raise BrowserError(E_TAB_EMPTY.format(tab=tab.id))
    try:
        return engine.perform(action, ctx), None
    except BrowserError as exc:
        if engine.name != "http" or not _needs_chrome(exc) or tab.mode == "http":
            raise
        binary, problem = _chrome_status()
        if binary is None:
            raise BrowserUnavailable(chrome_missing_message(label, problem)) from None
        return None, chrome_only_guard(session, tab, label, ctx, action.deadline, max_chars=max_chars)


def _changes_text(before: Any, page: views.ViewPage) -> str:
    after = views.semantic_snapshot(page)
    return views.render_changes(before, after, ref_order=REF_PREFIX_ORDER)


def _after_action(
    session: BrowserSession,
    tab: Tab,
    before: Any,
    outcome: ActionOutcome,
    ctx: Any,
    *,
    view: str | None,
    max_chars: int,
    notes: list[str],
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    more_notes, more_extra = _apply(session, tab, outcome, ctx)
    all_notes = _merge_notes(notes, outcome.notes, more_notes)
    if outcome.selected:
        more_extra["selected"] = list(outcome.selected)
    if extra:
        more_extra.update(extra)
    popups = more_extra.get("popups") or []
    opened = more_extra.get("opened_tab") or (popups[-1] if popups else None)
    shown = session.tabs.get(opened) if opened else None
    if shown is None:
        shown = tab
    if outcome.navigated or shown is not tab:
        if shown is tab and outcome.page is not None and tab.engine is not None and tab.engine.name == "http":
            page = outcome.page
        else:
            page = current_page(session, shown)
        return page_result(
            session,
            shown,
            page,
            view=view or "content",
            max_chars=max_chars,
            navigated=True,
            notes=all_notes,
            extra=more_extra,
        )
    page = current_page(session, tab)
    if view:
        return page_result(
            session, tab, page, view=view, max_chars=max_chars, navigated=False, notes=all_notes, extra=more_extra
        )
    return page_result(
        session,
        tab,
        page,
        view="changes",
        max_chars=max_chars,
        navigated=False,
        notes=all_notes,
        extra=more_extra,
        text=_changes_text(before, page),
    )


# ---------------------------------------------------------------------------
# browser_open


def _normalize_open_url(raw: str) -> tuple[str, list[str]]:
    text = raw.strip()
    lowered = text.lower()
    if lowered == "about:blank" or lowered.startswith("about:blank#"):
        return text, []
    if text.startswith("//"):
        url = "https:" + text
        return url, [f"Адрес без схемы: открыт {display_url(url)}"]
    match = re.match(r"^([A-Za-z][A-Za-z0-9+.\-]*):(.*)$", text, re.S)
    if match:
        scheme = match.group(1).lower()
        rest = match.group(2)
        if scheme in _OPEN_SCHEMES:
            return text, []
        port_like = re.match(r"^\d+(?:[/?#]|$)", rest) is not None
        if not port_like and (scheme in _KNOWN_SCHEMES or rest.startswith("//") or "." not in scheme):
            raise BrowserError(E_URL_SCHEME)
    url = "https://" + text
    return url, [f"Адрес без схемы: открыт {display_url(url)}"]


def _wait_until(args: Mapping[str, Any]) -> str | None:
    return _choice(args, "wait_until", _WAIT_UNTIL)


def op_open(session: BrowserSession, args: dict, ctx: Any) -> dict[str, Any]:
    raw = arg_str(args, "url")
    if not raw:
        raise BrowserError(E_URL_SCHEME)
    url, notes = _normalize_open_url(raw)
    password = userinfo_password(url)
    if password:
        ctx.secret(password)
    headers = arg_pairs(args, "headers")
    for name, value in headers:
        _secret_header(ctx, name, value)
    engine_mode = _choice(args, "engine", ENGINE_MODES)
    view = _choice(args, "view", tuple(views.NAV_VIEWS), "content")
    max_chars = _non_negative(args, "max_chars")
    deadline = _deadline(args)
    tab_arg = arg_str(args, "tab")
    if arg_bool(args, "new_tab", False) or not session.tabs:
        tab = session.new_tab(mode=engine_mode)
    else:
        tab = session.get_tab(tab_arg)
        session.current_tab = tab.id
    if engine_mode:
        tab.mode = engine_mode
    navigation = Navigation(
        url=url,
        method="GET",
        headers=headers,
        initiator="typed",
        wait_until=_wait_until(args),
        deadline=deadline,
        max_redirects=_non_negative(args, "max_redirects"),
        max_bytes=_non_negative(args, "max_bytes"),
    )
    outcome, nav_notes = _navigate(session, tab, navigation, ctx)
    more_notes, extra = _apply(session, tab, outcome, ctx)
    return page_result(
        session,
        tab,
        outcome.page,
        view=view or "content",
        max_chars=max_chars,
        navigated=outcome.navigated,
        notes=_merge_notes(notes, nav_notes, outcome.notes, more_notes),
        extra=extra,
    )


# ---------------------------------------------------------------------------
# browser_read, browser_find, browser_extract


def _render_kwargs(args: Mapping[str, Any], scope: list[Element] | None) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    if scope is not None:
        kwargs["scope"] = scope
    for name in ("section", "filter", "json_path"):
        value = arg_str(args, name)
        if value is not None:
            kwargs[name] = value
    if arg_bool(args, "inline_urls", False):
        kwargs["inline_urls"] = True
    if arg_bool(args, "include_hidden", False):
        kwargs["include_hidden"] = True
    return kwargs


def op_read(session: BrowserSession, args: dict, ctx: Any) -> dict[str, Any]:
    tab = session.get_tab(arg_str(args, "tab"))
    page = current_page(session, tab)
    view = _choice(args, "view", tuple(views.VIEWS), "content") or "content"
    max_chars = _non_negative(args, "max_chars")
    offset = _optional_offset(args)
    selector = arg_str(args, "selector")
    ref = arg_str(args, "ref")
    if selector and ref:
        raise BrowserError(E_VIEW_PARAM.format(param="selector вместе с ref", view=view))
    parsed = parse_ref(ref) if ref else None
    if ref and parsed is not None and parsed[0] == "N":
        engine = tab.engine
        wanted = normalize_ref(ref)
        entries = engine.network() if engine is not None else []
        entry = next((item for item in entries if item.get("id") == wanted), None)
        if entry is None:
            raise BrowserNotFound(
                f"Запрос {wanted} не найден; список запросов: browser_read(view='network')"
            )
        body, charset = engine.response_body(wanted)
        body_text = body.decode(charset or "utf-8", errors="replace") if body else ""
        return page_result(
            session,
            tab,
            page,
            view="network",
            max_chars=max_chars,
            offset=offset,
            navigated=False,
            text=views.render_network_entry(entry, body_text),
        )
    if (selector or ref) and view not in views.DOM_VIEWS:
        raise BrowserError(E_VIEW_PARAM.format(param="selector" if selector else "ref", view=view))
    page, scope = view_scope(session, tab, page, selector, ref)
    kwargs = _render_kwargs(args, scope)
    engine = tab.engine
    if view == "network" and engine is not None:
        kwargs["network"] = engine.network()
    if view == "console" and engine is not None:
        kwargs["console"] = engine.console()
    return page_result(
        session,
        tab,
        page,
        view=view,
        max_chars=max_chars,
        offset=offset,
        navigated=False,
        render_kwargs=kwargs,
    )


def _markdown_headings(text: str) -> list[tuple[int, int, str]]:
    """(offset, level, title) of the Markdown headings in ``text`` outside code fences."""
    found: list[tuple[int, int, str]] = []
    position = 0
    fenced = False
    for line in text.splitlines(keepends=True):
        stripped = line.rstrip("\r\n")
        if stripped.startswith("```"):
            fenced = not fenced
        elif not fenced:
            match = _MARKDOWN_HEADING.match(stripped)
            if match:
                found.append((position, len(match.group(1)), match.group(2).strip()))
        position += len(line)
    return found


def _find_headings(page: views.ViewPage, view: str, text: str) -> list[tuple[int, str, int, str]]:
    """Section headings of the searched text with the outline numbers of the page."""
    if page.model is None or page.kind == "binary":
        return []
    try:
        if view == "content":
            rendered = page.rendered_content()
        elif view == "text":
            rendered = page.rendered_text()
        else:
            rendered = page.rendered_page()
        page_headings = list(page.rendered_page().headings or [])
    except BrowserError:
        return []
    if rendered.text == text:
        return list(rendered.headings or [])
    result: list[tuple[int, str, int, str]] = []
    cursor = 0
    for offset, level, title in _markdown_headings(text):
        number = ""
        for index in range(cursor, len(page_headings)):
            if str(page_headings[index][3]).strip() == title:
                number = str(page_headings[index][1])
                cursor = index + 1
                break
        result.append((offset, number, level, title))
    return result


def op_find(session: BrowserSession, args: dict, ctx: Any) -> dict[str, Any]:
    query = arg_raw_str(args, "query")
    if query is None or not query.strip():
        raise BrowserError(E_ARG_TYPE.format(name="query", expected="непустой строкой", value=query))
    tab = session.get_tab(arg_str(args, "tab"))
    page = current_page(session, tab)
    view = _choice(args, "view", ("page", "content", "text", "html"), "page") or "page"
    max_chars = _non_negative(args, "max_chars")
    offset = _optional_offset(args)
    context_chars = arg_int(args, "context_chars")
    if context_chars is not None and context_chars < 0:
        raise BrowserError(E_ARG_NEGATIVE.format(name="context_chars", value=context_chars))
    if page.kind == "binary":
        raise _binary_error(tab, page)
    text, notes = views.render_view(page, view)
    headings = [] if view == "html" else _find_headings(page, view, text)
    matches = views.find_matches(
        text,
        query,
        regex=arg_bool(args, "regex", False),
        case_sensitive=arg_bool(args, "case_sensitive", False),
        context_chars=context_chars,
        headings=headings,
    )
    rendered = views.render_find(matches, query=query, view=view)
    return page_result(
        session,
        tab,
        page,
        view=view,
        max_chars=max_chars,
        offset=offset,
        navigated=False,
        notes=notes,
        extra={"matches": len(matches)},
        text=rendered,
    )


def op_extract(session: BrowserSession, args: dict, ctx: Any) -> dict[str, Any]:
    selector = arg_str(args, "selector")
    if not selector:
        raise BrowserError(E_ARG_TYPE.format(name="selector", expected="CSS-селектором", value=selector))
    tab = session.get_tab(arg_str(args, "tab"))
    page = current_page(session, tab)
    output = _choice(args, "output", ("text", "markdown", "html", "attributes"), "text") or "text"
    attributes_arg = arg_list(args, "attributes")
    attributes = [str(item) for item in attributes_arg] if attributes_arg is not None else None
    include_hidden = arg_bool(args, "include_hidden", False)
    max_chars = _non_negative(args, "max_chars")
    offset = _optional_offset(args)
    page, found = view_scope(session, tab, page, selector, None, require=False)
    elements = list(found or [])
    model = page.model
    shown = elements
    if not include_hidden and model is not None:
        shown = [element for element in elements if not model.is_hidden(element)]
    text = views.render_extract(
        page,
        elements,
        selector=selector,
        output=output,
        attributes=attributes,
        include_hidden=include_hidden,
    )
    return page_result(
        session,
        tab,
        page,
        view="extract",
        max_chars=max_chars,
        offset=offset,
        navigated=False,
        extra={"count": len(shown)},
        text=text,
    )


# ---------------------------------------------------------------------------
# browser_click, browser_act, browser_fill_form


def op_click(session: BrowserSession, args: dict, ctx: Any) -> dict[str, Any]:
    target = _target(args)
    has_point = target.x is not None or target.y is not None
    if has_point and (target.x is None or target.y is None):
        raise BrowserError(E_TARGET_REQUIRED)
    chosen = sum(bool(item) for item in (target.ref, target.text, target.selector, has_point))
    if chosen != 1:
        raise BrowserError(E_TARGET_REQUIRED)
    tab = session.get_tab(arg_str(args, "tab"))
    deadline = _deadline(args)
    _ensure_live(session, tab, action="browser_click", deadline=deadline, ctx=ctx)
    page = current_page(session, tab)
    max_chars = _non_negative(args, "max_chars")
    view = _choice(args, "view", tuple(views.NAV_VIEWS))
    before = views.semantic_snapshot(page)
    action = Action(
        kind="click",
        target=target,
        force=arg_bool(args, "force", False),
        new_tab=arg_bool(args, "new_tab", False),
        wait_until=_wait_until(args),
        deadline=deadline,
        max_redirects=_non_negative(args, "max_redirects"),
        max_bytes=_non_negative(args, "max_bytes"),
    )
    engine_name = tab.engine.name if tab.engine is not None else ""
    outcome, reopened = _perform(session, tab, action, ctx, label="browser_click", max_chars=max_chars)
    if reopened is not None:
        return reopened
    assert outcome is not None
    ctx.event(
        "browser_action",
        {
            "tab": tab.id,
            "engine": engine_name,
            "action": "click",
            "target": target.describe(),
            "navigated": bool(outcome.navigated),
        },
    )
    return _after_action(session, tab, before, outcome, ctx, view=view, max_chars=max_chars, notes=[])


def _is_secret_field(model: Any, element: Element) -> bool:
    try:
        kind = model.field_kind(element)
    except Exception:
        kind = ""
    return kind == "password" or is_sensitive_name(element.get("name"))


def op_act(session: BrowserSession, args: dict, ctx: Any) -> dict[str, Any]:
    kinds = ("type", "press", "hover", "scroll", "select", "check", "uncheck", "focus", "clear")
    kind = _choice(args, "action", kinds)
    if kind is None:
        raise BrowserError(E_ARG_TYPE.format(name="action", expected="одним из: " + ", ".join(kinds), value=None))
    target = _target(args)
    target.x = None
    target.y = None
    value = arg_raw_str(args, "value")
    submit = arg_bool(args, "submit", False)
    if kind not in ("press", "scroll") and target.is_empty():
        raise BrowserError(E_TARGET_REQUIRED)
    if kind == "press" and not (value or "").strip():
        raise BrowserError(
            E_ARG_TYPE.format(name="value", expected="клавишами, например Enter или Control+A", value=value)
        )
    if kind == "select" and value is None:
        raise BrowserError(
            E_ARG_TYPE.format(name="value", expected="вариантом списка (несколько — через перевод строки)", value=value)
        )
    tab = session.get_tab(arg_str(args, "tab"))
    deadline = _deadline(args)
    _ensure_live(session, tab, action="browser_act", deadline=deadline, ctx=ctx)
    page = current_page(session, tab)
    max_chars = _non_negative(args, "max_chars")
    view = _choice(args, "view", tuple(views.NAV_VIEWS))
    wait_until = _wait_until(args)
    element_target = None if target.is_empty() else target
    if kind == "type" and value and element_target is not None and page.model is not None:
        try:
            element, _notes = page.model.resolve_target(element_target, "I")
        except BrowserError:
            element = None
        if element is not None and _is_secret_field(page.model, element):
            ctx.secret(value)
    if kind == "type":
        action = Action(kind="type", target=element_target, value=value or "", replace=True, wait_until=wait_until, deadline=deadline)
    elif kind == "press":
        action = Action(kind="press", target=element_target, value=(value or "").strip(), wait_until=wait_until, deadline=deadline)
    elif kind == "scroll":
        action = Action(kind="scroll", target=element_target, value=(value or "").strip() or None, wait_until=wait_until, deadline=deadline)
    elif kind == "select":
        action = Action(kind="select", target=element_target, values=(value or "").split("\n"), wait_until=wait_until, deadline=deadline)
    else:
        action = Action(kind=kind, target=element_target, value=value, wait_until=wait_until, deadline=deadline)
    before = views.semantic_snapshot(page)
    engine_name = tab.engine.name if tab.engine is not None else ""
    outcome, reopened = _perform(session, tab, action, ctx, label=f"browser_act {kind}", max_chars=max_chars)
    if reopened is not None:
        return reopened
    assert outcome is not None
    notes: list[str] = []
    if kind == "type" and submit and not outcome.navigated:
        notes.extend(outcome.notes)
        selected = list(outcome.selected)
        follow = Action(kind="press", target=element_target, value="Enter", wait_until=wait_until, deadline=deadline)
        outcome, reopened = _perform(session, tab, follow, ctx, label="browser_act press", max_chars=max_chars)
        if reopened is not None:
            return reopened
        assert outcome is not None
        if selected and not outcome.selected:
            outcome.selected = selected
    ctx.event(
        "browser_action",
        {
            "tab": tab.id,
            "engine": engine_name,
            "action": kind + ("+submit" if kind == "type" and submit else ""),
            "target": target.describe() if element_target is not None else "страница",
            "navigated": bool(outcome.navigated),
        },
    )
    return _after_action(session, tab, before, outcome, ctx, view=view, max_chars=max_chars, notes=notes)


def _css_string(value: str) -> str:
    return (
        value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\a ").replace("\r", "\\d ")
    )


def _element_target(model: Any, element: Element) -> ElementTarget:
    """A target that finds ``element`` again: its ref, else a selector with nth."""
    ref = model.ref_of(element)
    if ref:
        return ElementTarget(ref=ref)
    element_id = element.get("id")
    name = element.get("name")
    if element_id:
        selector = f'{element.tag}[id="{_css_string(element_id)}"]'
    elif name:
        selector = f'{element.tag}[name="{_css_string(name)}"]'
    else:
        selector = element.tag
    matches = css.select(model.document, selector)
    nth = next((position for position, item in enumerate(matches, 1) if item is element), 1)
    return ElementTarget(selector=selector, nth=nth if len(matches) > 1 else None)


def _pick_in_group(model: Any, elements: list[Element], value: str | None, spec: str) -> list[Element]:
    if value is None:
        if len(elements) == 1:
            return elements
        options = ", ".join(f"«{model.field_value(element)}»" for element in elements)
        raise BrowserError(
            E_OPTION_NOT_FOUND.format(ref=model.ref_label(elements[0]), value="(не указан value)", options=options)
        )
    for element in elements:
        if model.field_value(element) == value:
            return [element]
    folded = value.strip().casefold()
    for element in elements:
        if model.accessible_name(element).strip().casefold() == folded:
            return [element]
    options = ", ".join(f"«{model.field_value(element)}»" for element in elements)
    raise BrowserError(E_OPTION_NOT_FOUND.format(ref=model.ref_label(elements[0]), value=value, options=options))


def _filled_line(model: Any, element: Element, description: str) -> str:
    label = model.accessible_name(element) or element.get("name") or model.field_kind(element)
    return f"[{model.ref_label(element)}] «{label}» {description}"


def op_fill_form(session: BrowserSession, args: dict, ctx: Any) -> dict[str, Any]:
    items = args.get("fields")
    if not isinstance(items, (list, tuple)) or not items:
        raise BrowserError(E_ARG_TYPE.format(name="fields", expected="непустым списком полей", value=items))
    tab = session.get_tab(arg_str(args, "tab"))
    deadline = _deadline(args)
    _ensure_live(session, tab, action="browser_fill_form", deadline=deadline, ctx=ctx)
    page = current_page(session, tab)
    max_chars = _non_negative(args, "max_chars")
    view = _choice(args, "view", tuple(views.NAV_VIEWS))
    wait_until = _wait_until(args)
    engine = tab.engine
    assert engine is not None
    form_element: Element | None = None
    form_arg = arg_str(args, "form")
    if page.model is None:
        raise _binary_error(tab, page)
    if form_arg:
        parsed = parse_ref(form_arg)
        if parsed is not None:
            found = page.model.element(normalize_ref(form_arg))
        else:
            page, matches = view_scope(session, tab, page, form_arg, None)
            found = (matches or [None])[0]
        model = page.model
        if found is None or model is None:
            raise BrowserNotFound(E_SELECTOR_NONE.format(sel=form_arg))
        form_element = found if found.tag == "form" else forms.form_of(model, found)
        if form_element is None:
            raise BrowserNotFound(
                f"«{form_arg}» не является формой и не находится в форме; формы страницы: browser_read(view='forms')"
            )
    model = page.model
    assert model is not None
    before = views.semantic_snapshot(page)
    plan: list[tuple[Mapping[str, Any], str, list[Element]]] = []
    for position, item in enumerate(items):
        if not isinstance(item, Mapping):
            raise BrowserError(
                E_ARG_TYPE.format(name=f"fields[{position}]", expected="объектом {field, value…}", value=item)
            )
        spec = str(item.get("field") or "").strip()
        if not spec:
            raise BrowserError(
                E_ARG_TYPE.format(name=f"fields[{position}].field", expected="непустой строкой", value=item.get("field"))
            )
        try:
            elements = forms.find_field(model, form_element, spec)
        except BrowserNotFound as exc:
            if form_element is None:
                raise
            try:
                elsewhere = forms.find_field(model, None, spec)
            except BrowserNotFound:
                raise exc from None
            owner = forms.form_of(model, elsewhere[0])
            if owner is form_element:
                raise exc from None
            raise BrowserError(
                E_FORM_MIXED.format(
                    forms=f"{model.ref_label(form_element)}, {model.ref_label(owner) if owner is not None else 'вне форм'}"
                )
            ) from None
        owner = forms.form_of(model, elements[0])
        if position == 0 and form_element is None:
            form_element = owner
        elif owner is not form_element:
            labels = [
                model.ref_label(item_form) if item_form is not None else "вне форм"
                for item_form in (form_element, owner)
            ]
            raise BrowserError(E_FORM_MIXED.format(forms=", ".join(labels)))
        plan.append((item, spec, elements))
    notes: list[str] = []
    filled: list[str] = []
    names: list[str] = []
    for item, spec, elements in plan:
        element = elements[0]
        kind = model.field_kind(element)
        value = item.get("value")
        values = item.get("values")
        checked = item.get("checked")
        file_paths = item.get("file_paths")
        value = None if value is None else str(value)
        secret = any(_is_secret_field(model, candidate) for candidate in elements)
        if file_paths:
            paths = [files_mod.resolve_input_path(str(path)) for path in file_paths]
            action = Action(kind="set_files", target=_element_target(model, element), values=paths, deadline=deadline)
            description = "файлы: " + ", ".join(os.path.basename(path) for path in paths)
        elif checked is not None:
            chosen = _pick_in_group(model, elements, value, spec)[0]
            element = chosen
            action = Action(
                kind="check" if bool(checked) else "uncheck",
                target=_element_target(model, chosen),
                deadline=deadline,
            )
            description = "✓ отмечен" if bool(checked) else "✗ снят"
        elif values is not None:
            selected = [str(entry) for entry in (values if isinstance(values, (list, tuple)) else [values])]
            action = Action(kind="select", target=_element_target(model, element), values=selected, deadline=deadline)
            description = "= " + ", ".join(f"«{entry}»" for entry in selected)
            if secret:
                for entry in selected:
                    ctx.secret(entry)
        elif value is not None:
            if element.tag == "select":
                action = Action(kind="select", target=_element_target(model, element), values=[value], deadline=deadline)
                description = f"= «{value}»"
            elif kind in ("checkbox", "radio"):
                chosen = _pick_in_group(model, elements, value, spec)[0]
                element = chosen
                action = Action(kind="check", target=_element_target(model, chosen), deadline=deadline)
                description = f"✓ отмечен вариант «{value}»"
            else:
                action = Action(
                    kind="set_value", target=_element_target(model, element), value=value, replace=True, deadline=deadline
                )
                if kind == "password":
                    description = f"= (заполнено, {len(value)} симв.)" if value else "= (пусто)"
                elif secret:
                    description = f"= (скрыто, {len(value)} симв.)"
                else:
                    description = f'= "{value}"'
            if secret:
                ctx.secret(value)
        else:
            raise BrowserError(
                E_ARG_TYPE.format(
                    name=f"поле «{spec}»", expected="с value, values, checked или file_paths", value=dict(item)
                )
            )
        outcome = engine.perform(action, ctx)
        notes.extend(outcome.notes)
        filled.append(_filled_line(model, element, description))
        field_name = element.get("name") or spec
        if field_name not in names:
            names.append(field_name)
    if arg_bool(args, "submit", False):
        if form_element is None:
            raise BrowserError(E_PRESS_NO_FORM.format(ref=model.ref_label(plan[0][2][0])))
        info = forms.form_info(model, form_element)
        submitter = None
        button = arg_str(args, "submit_button")
        if button:
            submitter = (
                ElementTarget(ref=normalize_ref(button)) if parse_ref(button) else ElementTarget(text=button)
            )
        if engine.name == "http" and info.has_onsubmit:
            notes.append("Форма может обрабатываться JavaScript; если результат неверный — engine='chrome'")
        action = Action(
            kind="submit",
            target=_element_target(model, form_element),
            submitter=submitter,
            wait_until=wait_until,
            deadline=deadline,
            max_redirects=_non_negative(args, "max_redirects"),
            max_bytes=_non_negative(args, "max_bytes"),
        )
        outcome, reopened = _perform(session, tab, action, ctx, label="browser_fill_form", max_chars=max_chars)
        if reopened is not None:
            return reopened
        assert outcome is not None
        ctx.event(
            "browser_form_submit",
            {
                "tab": tab.id,
                "form": info.ref or "",
                "method": (info.method or "get").upper(),
                "action": display_url(info.action),
                "enctype": info.enctype,
                "field_names": names,
            },
        )
        return _after_action(
            session,
            tab,
            before,
            outcome,
            ctx,
            view=view,
            max_chars=max_chars,
            notes=notes,
            extra={"filled": filled},
        )
    page_after = current_page(session, tab)
    model_after = page_after.model
    if form_element is not None and model_after is not None:
        form_after = form_element
        form_ref = model.ref_of(form_element)
        if model_after is not model and form_ref:
            try:
                form_after = model_after.element(form_ref)
            except BrowserError:
                form_after = None
        if form_after is not None:
            notes.extend(forms.validation_notes(model_after, form_after))
    notes.append("Форма не отправлена: submit=true или browser_click по кнопке отправки")
    if view:
        return page_result(
            session,
            tab,
            page_after,
            view=view,
            max_chars=max_chars,
            navigated=False,
            notes=_merge_notes(notes),
            extra={"filled": filled},
        )
    text = "Заполнено:\n" + "\n".join(filled) + "\n\nИзменения на странице:\n" + _changes_text(before, page_after)
    return page_result(
        session,
        tab,
        page_after,
        view="changes",
        max_chars=max_chars,
        navigated=False,
        notes=_merge_notes(notes),
        extra={"filled": filled},
        text=text,
    )


# ---------------------------------------------------------------------------
# browser_wait, browser_evaluate, browser_screenshot


def op_wait(session: BrowserSession, args: dict, ctx: Any) -> dict[str, Any]:
    tab = session.get_tab(arg_str(args, "tab"))
    engine = tab.engine
    if engine is None:
        raise BrowserError(E_TAB_EMPTY.format(tab=tab.id))
    deadline = _deadline(args)
    seconds = arg_float(args, "seconds")
    if seconds is not None and seconds < 0:
        raise BrowserError(E_ARG_NEGATIVE.format(name="seconds", value=seconds))
    request = WaitRequest(
        selector=arg_str(args, "selector"),
        selector_state=_choice(args, "selector_state", ("visible", "attached", "hidden", "detached"), "visible")
        or "visible",
        text=arg_str(args, "text"),
        text_gone=arg_str(args, "text_gone"),
        url_contains=arg_str(args, "url_contains"),
        expression=arg_str(args, "expression"),
        network_idle=arg_bool(args, "network_idle", False),
        seconds=seconds,
        deadline=deadline,
    )
    if engine.name == "chrome":
        _ensure_live(session, tab, action="browser_wait", deadline=deadline, ctx=ctx)
        _check_lost(tab)
    try:
        result = engine.wait(request, ctx)
    except BrowserError as exc:
        if engine.name == "http" and _needs_chrome(exc) and tab.mode != "http":
            binary, problem = _chrome_status()
            if binary is None:
                raise BrowserUnavailable(chrome_missing_message("browser_wait", problem)) from None
            return chrome_only_guard(session, tab, "browser_wait", ctx, deadline)
        raise
    notes, extra = _apply(session, tab, None, ctx)
    data: dict[str, Any] = {"session": session.key, "tab": tab.id, "engine": engine.name}
    data.update(result or {})
    data.update(extra)
    if notes:
        data["notes"] = _merge_notes(data.get("notes"), notes)
    return data


def op_evaluate(session: BrowserSession, args: dict, ctx: Any) -> dict[str, Any]:
    max_chars = _non_negative(args, "max_chars")
    offset = _optional_offset(args)
    result_id = arg_str(args, "result_id")
    if result_id:
        key = result_id.strip().upper()
        text = session.evaluations.get(key)
        if text is None:
            raise BrowserNotFound(
                E_RESULT_ID.format(id=result_id, ids=", ".join(session.evaluations) or "нет")
            )
        data = {"session": session.key, "result_id": key}
        data.update(paginate(text, offset, max_chars))
        return data
    expression = arg_raw_str(args, "expression")
    if expression is None or not expression.strip():
        raise BrowserError(E_EVAL_REQUIRED)
    tab = session.get_tab(arg_str(args, "tab"))
    engine = tab.engine
    if engine is None:
        raise BrowserError(E_TAB_EMPTY.format(tab=tab.id))
    deadline = _deadline(args)
    if engine.name == "http":
        return chrome_only_guard(session, tab, "browser_evaluate", ctx, deadline, max_chars=max_chars)
    _ensure_live(session, tab, action="browser_evaluate", deadline=deadline, ctx=ctx)
    _check_lost(tab)
    ref = arg_str(args, "ref")
    selector = arg_str(args, "selector")
    target = None
    if ref or selector:
        target = ElementTarget(ref=normalize_ref(ref) if ref else None, selector=selector)
    outcome = engine.evaluate(
        expression,
        target=target,
        await_promise=arg_bool(args, "await_promise", True),
        deadline=deadline,
    )
    value = outcome.value
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, indent=2, ensure_ascii=False, default=str)
    key = session.store_evaluation(text)
    ctx.event(
        "browser_evaluate",
        {"tab": tab.id, "result_id": key, "type": outcome.type, "chars": len(text)},
    )
    notes, extra = _apply(session, tab, None, ctx)
    data: dict[str, Any] = {
        "session": session.key,
        "tab": tab.id,
        "engine": engine.name,
        "result_id": key,
        "type": outcome.type,
    }
    data.update(paginate(text, offset, max_chars))
    if outcome.console:
        data["console"] = [str(line) for line in outcome.console]
    data.update(extra)
    if notes:
        data["notes"] = notes
    return data


def op_screenshot(session: BrowserSession, args: dict, ctx: Any) -> dict[str, Any]:
    path = arg_str(args, "path")
    overwrite = arg_bool(args, "overwrite", False)
    tab = session.get_tab(arg_str(args, "tab"))
    engine = tab.engine
    if engine is None:
        raise BrowserError(E_TAB_EMPTY.format(tab=tab.id))
    deadline = _deadline(args)
    if engine.name == "http":
        return chrome_only_guard(session, tab, "browser_screenshot", ctx, deadline)
    _ensure_live(session, tab, action="browser_screenshot", deadline=deadline, ctx=ctx)
    _check_lost(tab)
    fmt = _choice(args, "format", ("png", "jpeg", "pdf"))
    if fmt is None:
        raw = str(path or "")
        if not raw.endswith(("/", os.sep)):
            fmt = _CAPTURE_EXTENSIONS.get(os.path.splitext(raw)[1].lower())
        if fmt is None:
            raise BrowserError(E_CAPTURE_FORMAT)
    extension = "jpg" if fmt == "jpeg" else fmt
    stamp = time.strftime("%Y%m%d-%H%M%S")
    final = resolve_target(
        path,
        suggested_name=f"{tab.id}-{stamp}.{extension}",
        content_type=_CAPTURE_MIMES[fmt],
        overwrite=overwrite,
    )
    ref = arg_str(args, "ref")
    selector = arg_str(args, "selector")
    target = None
    if ref or selector:
        target = ElementTarget(ref=normalize_ref(ref) if ref else None, selector=selector)
    quality = arg_int(args, "quality")
    if quality is not None and not 0 <= quality <= 100:
        raise BrowserError(E_ARG_TYPE.format(name="quality", expected="числом от 0 до 100", value=quality))
    scale = arg_float(args, "scale")
    if scale is not None and scale <= 0:
        raise BrowserError(E_ARG_TYPE.format(name="scale", expected="числом больше 0", value=scale))
    request = CaptureRequest(
        format=fmt,
        full_page=arg_bool(args, "full_page", False),
        target=target,
        quality=quality,
        landscape=arg_bool(args, "landscape", False),
        print_background=arg_bool(args, "print_background", False),
        paper=_choice(args, "paper", ("A3", "A4", "A5", "Letter", "Legal", "Tabloid")),
        scale=scale,
        page_ranges=arg_str(args, "page_ranges"),
        deadline=deadline,
    )
    with AtomicWriter(final, overwrite=overwrite) as writer:
        outcome = engine.capture(request, writer)
        size, _sha256 = writer.commit()
    ctx.event("browser_capture", {"tab": tab.id, "format": fmt, "path": final, "bytes": size})
    data: dict[str, Any] = {
        "session": session.key,
        "tab": tab.id,
        "path": final,
        "format": fmt,
        "bytes": size,
        "size": human_bytes(size),
        "width": getattr(outcome, "width", None),
        "height": getattr(outcome, "height", None),
        "free_bytes": disk_free(os.path.dirname(final)),
    }
    notes, extra = _apply(session, tab, None, ctx)
    data.update(extra)
    if notes:
        data["notes"] = notes
    return data


# ---------------------------------------------------------------------------
# browser_history, browser_tabs


def _history_line(item: Any) -> str:
    marker = "→" if item.current else " "
    status = item.status if item.status is not None else "—"
    title = f"«{item.title}» " if item.title else ""
    return f"{marker} {item.index + 1}. [{item.engine}] {item.method} {status} {title}{item.url}"


def _previous_summary(tab: Tab) -> str:
    return ", ".join(
        f"[{item.engine}] {('«' + item.title + '» ') if item.title else ''}{item.url}" for item in tab.previous
    )


def op_history(session: BrowserSession, args: dict, ctx: Any) -> dict[str, Any]:
    action = _choice(args, "action", ("back", "forward", "reload", "list"))
    if action is None:
        raise BrowserError(E_ARG_TYPE.format(name="action", expected="back, forward, reload или list", value=None))
    tab = session.get_tab(arg_str(args, "tab"))
    max_chars = _non_negative(args, "max_chars")
    engine = tab.engine
    if action == "list":
        items = []
        if engine is not None and not tab.lost_reason:
            try:
                items = engine.history()
            except BrowserError:
                items = []
        lines = [_history_line(item) for item in items]
        if tab.previous:
            lines.append("")
            lines.append("Раньше в этой вкладке (другой движок):")
            lines.extend(_history_line(item).replace("→", " ", 1) for item in tab.previous)
        text = "\n".join(lines) if lines else "История вкладки пуста"
        data: dict[str, Any] = {
            "session": session.key,
            "tab": tab.id,
            "engine": engine.name if engine is not None else None,
            "mode": tab.mode,
            "view": "history",
            "entries": len(items),
        }
        data.update(paginate(text, None, max_chars))
        return data
    if engine is None:
        raise BrowserError(E_TAB_EMPTY.format(tab=tab.id))
    deadline = _deadline(args)
    _ensure_live(session, tab, action="browser_history", deadline=deadline, ctx=ctx)
    _check_lost(tab)
    view = _choice(args, "view", tuple(views.NAV_VIEWS), "content") or "content"
    wait_until = _wait_until(args)
    extra_kwargs: dict[str, Any] = {}
    if isinstance(engine, HttpTabEngine):
        if args.get("max_redirects") is not None:
            extra_kwargs["max_redirects"] = _non_negative(args, "max_redirects")
        if args.get("max_bytes") is not None:
            extra_kwargs["max_bytes"] = _non_negative(args, "max_bytes")
    if action == "reload":
        outcome = engine.reload(wait_until=wait_until, deadline=deadline, ctx=ctx, **extra_kwargs)
    else:
        steps = arg_int(args, "steps")
        if steps is None:
            steps = 1
        if steps < 1:
            raise BrowserError(E_ARG_TYPE.format(name="steps", expected="числом от 1", value=steps))
        items = engine.history()
        position = next((index for index, item in enumerate(items) if item.current), len(items) - 1)
        if action == "back" and position - steps < 0:
            raise BrowserError(history_back_message(tab.id, engine.name, _previous_summary(tab) or None))
        if action == "forward" and position + steps >= len(items):
            raise BrowserError(E_HISTORY_FWD.format(tab=tab.id))
        delta = -steps if action == "back" else steps
        outcome = engine.go(delta, wait_until=wait_until, deadline=deadline, ctx=ctx, **extra_kwargs)
    more_notes, extra = _apply(session, tab, outcome, ctx)
    return page_result(
        session,
        tab,
        outcome.page,
        view=view,
        max_chars=max_chars,
        navigated=outcome.navigated,
        notes=_merge_notes(outcome.notes, more_notes),
        extra=extra,
    )


def _tab_line(session: BrowserSession, tab: Tab) -> str:
    marker = "→" if tab.id == session.current_tab else " "
    engine = tab.engine
    engine_name = engine.name if engine is not None else "пусто"
    title = tab.last_title
    url = tab.last_url
    position = ""
    if engine is not None and not tab.lost_reason:
        try:
            items = engine.history()
        except Exception:
            items = []
        current = next((item for item in items if item.current), None)
        if current is not None:
            title = current.title or title
            url = current.url or url
            position = f" (история {current.index + 1} из {len(items)})"
    lost = f" — потеряна: {tab.lost_reason}" if tab.lost_reason else ""
    title_text = f" «{title}»" if title else ""
    url_text = f" {display_url(url)}" if url else ""
    return f"{marker} {tab.id} [{engine_name}, режим {tab.mode}]{title_text}{url_text}{position}{lost}"


def op_tabs(session: BrowserSession, args: dict, ctx: Any) -> dict[str, Any]:
    action = _choice(args, "action", ("list", "new", "switch", "close"))
    if action is None:
        raise BrowserError(E_ARG_TYPE.format(name="action", expected="list, new, switch или close", value=None))
    max_chars = _non_negative(args, "max_chars")
    if action == "new":
        engine_mode = _choice(args, "engine", ENGINE_MODES)
        tab = session.new_tab(mode=engine_mode)
        url = arg_str(args, "url")
        if url:
            open_args = dict(args)
            open_args["tab"] = tab.id
            open_args["new_tab"] = False
            open_args["view"] = arg_str(args, "view") or "none"
            open_args["max_chars"] = max_chars
            return op_open(session, open_args, ctx)
        data = {
            "session": session.key,
            "tab": tab.id,
            "mode": tab.mode,
            "tabs": len(session.tabs),
        }
        data.update(paginate(f"Открыта пустая вкладка {tab.id} (режим {tab.mode})", None, max_chars))
        return data
    if action == "switch":
        tab_arg = arg_str(args, "tab")
        if not tab_arg:
            raise BrowserError(E_ARG_TYPE.format(name="tab", expected="id вкладки (t1, t2…)", value=None))
        tab = session.get_tab(tab_arg)
        session.current_tab = tab.id
        if tab.engine is not None and tab.engine.has_page() and not tab.lost_reason:
            view = _choice(args, "view", tuple(views.NAV_VIEWS), "none") or "none"
            return page_result(
                session,
                tab,
                current_page(session, tab),
                view=view,
                max_chars=max_chars,
                navigated=False,
                notes=[f"Текущая вкладка: {tab.id}"],
            )
        data = {"session": session.key, "tab": tab.id, "mode": tab.mode, "tabs": len(session.tabs)}
        data.update(paginate(_tab_line(session, tab), None, max_chars))
        return data
    if action == "close":
        closing = session.get_tab(arg_str(args, "tab")).id
        current = session.close_tab(closing, ctx)
        text = f"Вкладка {closing} закрыта" + (f"; текущая {current}" if current else "; вкладок не осталось")
        data = {"session": session.key, "closed": closing, "current": current, "tabs": len(session.tabs)}
        data.update(paginate(text, None, max_chars))
        return data
    lines = [_tab_line(session, tab) for tab in session.tabs.values()]
    text = "\n".join(lines) if lines else "Вкладок нет: начните с browser_open(url=…)"
    data = {"session": session.key, "current": session.current_tab, "tabs": len(session.tabs)}
    data.update(paginate(text, None, max_chars))
    return data


# ---------------------------------------------------------------------------
# browser_cookies


def _cookie_line(row: Mapping[str, Any]) -> str:
    flags = []
    if row.get("secure"):
        flags.append("[secure]")
    if row.get("http_only"):
        flags.append("[httponly]")
    if row.get("same_site"):
        flags.append(f"[samesite={row['same_site']}]")
    expires = row.get("expires")
    lifetime = f"истекает {_iso_date(expires)}" if expires is not None and not row.get("session") else "до конца сессии"
    return (
        f"{row.get('name')}  {row.get('domain')}  {row.get('path')}  "
        + (" ".join(flags) + "  " if flags else "")
        + f"{lifetime} (значение скрыто, {row.get('length')} симв., отпечаток {row.get('fp')})"
    )


def op_cookies(session: BrowserSession, args: dict, ctx: Any) -> dict[str, Any]:
    action = _choice(args, "action", ("list", "set", "delete", "clear"))
    if action is None:
        raise BrowserError(E_ARG_TYPE.format(name="action", expected="list, set, delete или clear", value=None))
    client = session.http()
    jar = client.cookies
    domain = arg_str(args, "domain")
    url = arg_str(args, "url")
    max_chars = _non_negative(args, "max_chars")
    offset = _optional_offset(args)
    if action == "list":
        rows = cookie_rows(jar, domain=domain, url=url)
        text = "\n".join(_cookie_line(row) for row in rows) if rows else "Cookies нет"
        data: dict[str, Any] = {
            "session": session.key,
            "count": len(rows),
            "domains": sorted({str(row.get("domain") or "").lstrip(".") for row in rows}),
        }
        data.update(paginate(text, offset, max_chars))
        return data
    name = arg_str(args, "name")
    if action == "set":
        value = arg_raw_str(args, "value")
        if not name or value is None or not (domain or url):
            raise BrowserError(E_COOKIE_FIELDS)
        ctx.secret(value)
        expires = arg_float(args, "expires")
        set_cookie(
            jar,
            name=name,
            value=value,
            domain=None if url else domain,
            url=url,
            path=arg_str(args, "path") or "/",
            secure=arg_bool(args, "secure", False),
            http_only=arg_bool(args, "http_only", False),
            same_site=_choice(args, "same_site", ("Strict", "Lax", "None")),
            expires=expires,
        )
        count = 1
        text = f"Cookie {name} установлена для {domain or _host(url)}"
    elif action == "delete":
        count = delete_cookies(jar, name=name, domain=domain or (_host(url) if url else None), path=arg_str(args, "path"))
        text = f"Удалено cookies: {count}"
    else:
        count = clear_cookies(jar, domain=domain or (_host(url) if url else None))
        text = f"Удалено cookies: {count}"
    browser = session.chrome_if_running()
    notes: list[str] = []
    if browser is not None:
        try:
            browser.set_cookies(jar_to_cdp(jar))
            session.cookie_owner = "chrome"
        except BrowserError as exc:
            notes.append(f"Chrome не принял cookies: {exc}")
    ctx.event(
        "browser_cookie_change",
        {
            "action": action,
            "domain": domain or _host(url) or "",
            "names": [name] if name else [],
            "count": count,
        },
    )
    data = {"session": session.key, "action": action, "count": count}
    data.update(paginate(text, None, max_chars))
    if notes:
        data["notes"] = notes
    return data


# ---------------------------------------------------------------------------
# browser_search


def op_search(session: BrowserSession, args: dict, ctx: Any) -> dict[str, Any]:
    query = arg_str(args, "query")
    if not query:
        raise BrowserError(E_ARG_TYPE.format(name="query", expected="непустой строкой", value=query))
    engine_name = _choice(args, "engine", search_mod.ENGINES)
    if engine_name is None:
        raise BrowserError(
            E_ARG_TYPE.format(name="engine", expected="одним из: " + ", ".join(search_mod.ENGINES), value=None)
        )
    page_number = arg_int(args, "page") or 1
    if page_number < 1:
        raise BrowserError(E_ARG_TYPE.format(name="page", expected="числом от 1", value=page_number))
    page_size = arg_int(args, "page_size")
    if page_size is not None and page_size < 1:
        raise BrowserError(E_ARG_TYPE.format(name="page_size", expected="числом от 1", value=page_size))
    custom = None
    if engine_name == "custom":
        custom = search_mod.CustomEngine(
            url_template=arg_raw_str(args, "url_template") or "",
            result_selector=arg_str(args, "result_selector") or "",
            title_selector=arg_str(args, "title_selector"),
            link_selector=arg_str(args, "link_selector"),
            snippet_selector=arg_str(args, "snippet_selector"),
            page_size=page_size,
        )
    max_chars = _non_negative(args, "max_chars")
    offset = _optional_offset(args)
    deadline = _deadline(args)
    client = session.http()

    def fetch(spec: RequestSpec) -> Any:
        return client.load(spec)

    outcome = search_mod.search(
        fetch,
        query=query,
        engine=engine_name,
        page=page_number,
        region=arg_str(args, "region"),
        safe_search=_choice(args, "safe_search", search_mod.SAFE_SEARCH),
        time_range=_choice(args, "time_range", search_mod.TIME_RANGES),
        custom=custom,
        cursors=session.search_cursors,
        deadline=deadline,
    )
    notes = list(outcome.notes)
    if outcome.ads_skipped:
        notes.append(f"Рекламные блоки не включены: {outcome.ads_skipped}")
    data: dict[str, Any] = {
        "session": session.key,
        "engine": outcome.engine,
        "query": outcome.query,
        "page": outcome.page,
        "results": len(outcome.results),
        "ads_skipped": outcome.ads_skipped,
        "has_next": outcome.has_next,
    }
    tab_arg = arg_str(args, "tab")
    if tab_arg and outcome.documents:
        tab = session.new_tab(mode=None) if tab_arg.lower() == "new" else session.get_tab(tab_arg)
        session.current_tab = tab.id
        engine = session.engine_for(tab, "http", deadline=deadline, ctx=ctx, action="browser_search", reason="страница результатов поиска")
        if not isinstance(engine, HttpTabEngine):
            raise BrowserError("Вкладку для результатов поиска не удалось открыть движком http")
        shown = engine.push_document(outcome.documents[-1], initiator="search")
        tab.last_url = shown.url
        tab.last_title = shown.title
        data["tab"] = tab.id
        notes.append(
            f"Страница результатов загружена во вкладку {tab.id}: ссылки — browser_read(tab='{tab.id}', view='links')"
        )
    ctx.event(
        "browser_search",
        {
            "engine": outcome.engine,
            "query": outcome.query,
            "page": outcome.page,
            "results": len(outcome.results),
        },
    )
    data.update(paginate(search_mod.render_results(outcome), offset, max_chars))
    if notes:
        data["notes"] = notes
    return data


# ---------------------------------------------------------------------------
# browser_request


def _response_key(value: str) -> str:
    text = value.strip()
    if text[:1] in ("r", "R"):
        return "R" + text[1:].strip()
    return text


def _json_text(text: str, content_type: str, json_path: str | None, notes: list[str]) -> str:
    """Pretty JSON (or the json_path selection); a body that is not JSON stays as is."""
    try:
        value = json.loads(text)
    except ValueError as exc:
        if json_path:
            raise BrowserError(E_NOT_JSON.format(ct=content_type or "не указан", reason=str(exc))) from None
        notes.append(f"Тело помечено как JSON, но не разбирается ({exc}); показано как есть")
        return text
    if json_path:
        return views.render_json(value, json_path)
    return json.dumps(value, indent=2, ensure_ascii=False)


def _stored_view(stored: StoredResponse) -> dict[str, Any]:
    return {
        "response_id": stored.id,
        "method": stored.method,
        "url": stored.url,
        "status": stored.status,
        "reason": stored.reason,
        "http_version": stored.http_version,
        "redirects": hop_lines(stored.hops, stored.url) if stored.hops else [],
        "headers": [f"{name}: {value}" for name, value in stored.headers_masked],
        "content_type": stored.content_type,
        "kind": stored.kind,
        "charset": stored.charset,
        "bytes": stored.bytes,
        "elapsed_ms": stored.elapsed_ms,
    }


def _request_body(
    args: Mapping[str, Any], jar: Any, url: str, ctx: Any, has_content_type: bool
) -> tuple[bytes | None, Any, int | None, str | None, Callable[[], tuple[Any, str, int]] | None]:
    json_body = arg_raw_str(args, "json_body")
    form_given = args.get("form") is not None
    files_arg = arg_list(args, "files") or []
    body = arg_raw_str(args, "body")
    body_base64 = arg_raw_str(args, "body_base64")
    given = [json_body is not None, form_given or bool(files_arg), body is not None, body_base64 is not None]
    if sum(given) > 1:
        raise BrowserError(E_BODY_CONFLICT)

    def substitute(text: str) -> str:
        return substitute_cookie_placeholders(text, jar, url, ctx.secret)

    if json_body is not None:
        text = substitute(json_body)
        try:
            json.loads(text)
        except ValueError as exc:
            raise BrowserError(E_JSON_BODY.format(reason=str(exc))) from None
        return text.encode("utf-8"), None, None, None if has_content_type else "application/json", None
    if form_given or files_arg:
        pairs = [(name, substitute(value)) for name, value in arg_pairs(args, "form")]
        if files_arg:
            items: list[tuple[str, str, str | None, str | None]] = []
            for position, item in enumerate(files_arg):
                if not isinstance(item, Mapping) or not item.get("name") or not item.get("path"):
                    raise BrowserError(
                        E_ARG_TYPE.format(name=f"files[{position}]", expected="объектом {name, path}", value=item)
                    )
                items.append(
                    (
                        str(item["name"]),
                        files_mod.resolve_input_path(str(item["path"])),
                        str(item["content_type"]) if item.get("content_type") else None,
                        str(item["filename"]) if item.get("filename") else None,
                    )
                )

            def encode() -> tuple[Any, str, int]:
                return encode_multipart(pairs, items, "utf-8")

            body_file, content_type, length = encode()
            return None, body_file, length, content_type, encode
        encoded = urlencode(pairs).encode("ascii")
        return encoded, None, None, None if has_content_type else "application/x-www-form-urlencoded", None
    if body is not None:
        return substitute(body).encode("utf-8"), None, None, None, None
    if body_base64 is not None:
        compact = re.sub(r"\s+", "", body_base64)
        compact += "=" * (-len(compact) % 4)
        try:
            raw = base64.b64decode(compact, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise BrowserError(E_BAD_REQUEST.format(reason=f"body_base64 не является корректным base64 ({exc})")) from None
        return raw, None, None, None, None
    return None, None, None, None, None


def op_request(session: BrowserSession, args: dict, ctx: Any) -> dict[str, Any]:
    max_chars = _non_negative(args, "max_chars")
    offset = _optional_offset(args)
    json_path = arg_str(args, "json_path")
    response_id = arg_str(args, "response_id")
    if response_id:
        key = _response_key(response_id)
        stored = session.responses.get(key)
        if stored is None:
            raise BrowserNotFound(
                E_RESPONSE_ID.format(id=response_id, ids=", ".join(session.responses) or "нет")
            )
        notes: list[str] = []
        text = stored.text
        if json_path:
            text = _json_text(stored.text, stored.content_type, json_path, notes)
        data = {"session": session.key, **_stored_view(stored)}
        data.update(paginate(text, offset, max_chars))
        notes = _merge_notes(stored.notes, notes)
        if notes:
            data["notes"] = notes
        return data
    raw_url = arg_str(args, "url")
    if not raw_url:
        raise BrowserError(E_REQUEST_URL)
    url, url_notes = _normalize_open_url(raw_url)
    method = (arg_str(args, "method") or "GET").upper()
    password = userinfo_password(url)
    if password:
        ctx.secret(password)
    client = session.http()
    jar = client.cookies
    headers: list[tuple[str, str]] = []
    for name, value in arg_pairs(args, "headers"):
        value = substitute_cookie_placeholders(value, jar, url, ctx.secret)
        _secret_header(ctx, name, value)
        headers.append((name, value))
    has_content_type = any(name.lower() == "content-type" and value for name, value in headers)
    body, body_file, body_length, content_type, encode = _request_body(args, jar, url, ctx, has_content_type)
    deadline = _deadline(args)
    spec = RequestSpec(
        url=url,
        method=method,
        headers=headers,
        params=arg_pairs(args, "query"),
        body=body,
        body_file=body_file,
        body_length=body_length,
        content_type=content_type,
        mode="api",
        use_cookies=arg_bool(args, "use_cookies", True),
        follow_redirects=arg_bool(args, "follow_redirects", True),
        follow_refresh=False,
        max_redirects=_non_negative(args, "max_redirects"),
        max_bytes=_non_negative(args, "max_bytes"),
        deadline=deadline,
    )
    if encode is not None:
        encoder = encode

        def rebuild() -> RequestSpec:
            fresh_file, _ctype, fresh_length = encoder()
            return replace(spec, body_file=fresh_file, body_length=fresh_length)

        spec.rebuild = rebuild
    save_to = arg_str(args, "save_to")
    tab_arg = arg_str(args, "tab")
    notes = list(url_notes)
    if save_to and tab_arg:
        raise BrowserError(E_ARG_TYPE.format(name="save_to и tab", expected="только одним из параметров", value=f"{save_to}, {tab_arg}"))
    if save_to:
        try:
            saved = client.stream_to_file(spec, save_to, overwrite=arg_bool(args, "overwrite", False))
        finally:
            if body_file is not None:
                body_file.close()
        ctx.event(
            "browser_request",
            {
                "method": method,
                "url": saved["url"],
                "status": saved["status"],
                "bytes": saved["bytes"],
                "saved_to": saved["path"],
            },
        )
        data = {
            "session": session.key,
            "method": method,
            "url": saved["url"],
            "status": saved["status"],
            "redirects": saved["redirects"],
            "content_type": saved["content_type"],
            "bytes": saved["bytes"],
            "size": human_bytes(saved["bytes"]),
            "elapsed_ms": saved["elapsed_ms"],
            "saved_to": saved["path"],
            "sha256": saved["sha256"],
            "free_bytes": saved["free_bytes"],
        }
        data.update(paginate(f"Тело ответа сохранено: {saved['path']} ({human_bytes(saved['bytes'])})", None, max_chars))
        if notes:
            data["notes"] = notes
        return data
    if tab_arg:
        try:
            doc = client.load(spec)
        finally:
            if body_file is not None:
                body_file.close()
        tab = session.new_tab(mode=None) if tab_arg.lower() == "new" else session.get_tab(tab_arg)
        session.current_tab = tab.id
        engine = session.engine_for(tab, "http", deadline=deadline, ctx=ctx, action="browser_request", reason="ответ запроса во вкладке")
        if not isinstance(engine, HttpTabEngine):
            raise BrowserError("Вкладку для ответа не удалось открыть движком http")
        page = engine.push_document(doc, initiator="request")
        ctx.event(
            "browser_request",
            {"method": method, "url": display_url(doc.url), "status": doc.status, "bytes": doc.size, "saved_to": None},
        )
        return page_result(
            session,
            tab,
            page,
            view="content" if doc.kind != "json" else "json",
            max_chars=max_chars,
            offset=offset,
            navigated=True,
            notes=_merge_notes(notes, doc.notes),
            render_kwargs={"json_path": json_path} if json_path and doc.kind == "json" else None,
            extra={"method": method},
        )
    try:
        response = client.send(spec)
    finally:
        if body_file is not None:
            body_file.close()
    text = ""
    charset = ""
    size: int | None = 0
    kind = "empty"
    try:
        notes.extend(response.notes)
        if method == "HEAD":
            kind = "empty"
        else:
            head = response.peek(512) if response.mime in GENERIC_MIMES else None
            kind = classify(response.mime, head)
        if kind == "binary":
            size = response.content_length
            notes.append(
                f"Тело двоичное ({response.mime or 'application/octet-stream'}, {human_bytes(size)}); "
                "сохраните его: повторите запрос с save_to=…"
            )
            response.close()
        elif kind != "empty":
            raw, cut = response.read_body(spec.max_bytes)
            size = len(raw)
            if raw:
                text, charset, _source, had_errors = decode_body(raw, response.content_type, kind)
                if had_errors:
                    notes.append(f"в тексте есть неразборчивые байты ({charset})")
            else:
                kind = "empty"
            if cut:
                total = cut.get("content_length")
                notes.append(
                    E_BODY_CUT.format(
                        read=cut["bytes_read"],
                        total=total if total is not None else "неизвестно скольких",
                        n=spec.max_bytes,
                    )
                )
    finally:
        response.close()
    if kind == "json" and text:
        stored_text = _json_text(text, response.content_type, None, notes)
    else:
        stored_text = text
    if response.status >= 400:
        notes.append(f"Сервер ответил {response.status} {response.reason}".rstrip())
    stored = StoredResponse(
        id="",
        method=method,
        url=display_url(response.url),
        status=response.status,
        reason=response.reason,
        http_version=response.http_version,
        headers_masked=mask_headers(response.headers),
        hops=list(response.hops),
        content_type=response.content_type,
        charset=charset,
        kind=kind,
        text=stored_text,
        bytes=size,
        elapsed_ms=response.elapsed_ms,
        notes=list(notes),
    )
    session.store_response(stored)
    shown = stored_text
    if json_path:
        if kind != "json" and not text:
            raise BrowserError(E_NOT_JSON.format(ct=response.content_type or "не указан", reason="тело пустое"))
        shown = _json_text(text, response.content_type, json_path, notes)
    ctx.event(
        "browser_request",
        {"method": method, "url": stored.url, "status": stored.status, "bytes": size, "saved_to": None},
    )
    data = {"session": session.key, **_stored_view(stored)}
    data["redirects"] = hop_lines(stored.hops, response.url) if stored.hops else []
    data.update(paginate(shown, offset, max_chars))
    if notes:
        data["notes"] = _merge_notes(notes)
    return data


# ---------------------------------------------------------------------------
# browser_download


def _write_bytes(path: str | None, payload: bytes, mime: str, overwrite: bool, source_url: str) -> dict[str, Any]:
    started = time.monotonic()
    final = resolve_target(
        path, suggested_name="download" + extension_for(mime), content_type=mime, overwrite=overwrite
    )
    with AtomicWriter(final, overwrite=overwrite) as writer:
        writer.write(payload)
        size, sha256 = writer.commit()
    return {
        "path": final,
        "bytes": size,
        "sha256": sha256,
        "content_type": mime,
        "url": source_url,
        "status": None,
        "filename_source": "generated",
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "free_bytes": disk_free(os.path.dirname(final)),
        "redirects": [],
    }


def _copy_local(path: str | None, source: str, overwrite: bool, max_bytes: int) -> dict[str, Any]:
    started = time.monotonic()
    if not source:
        raise BrowserNotFound(E_FILE_MISSING.format(path=source))
    # The same path rule as file:// pages and the filesystem tools (host port).
    source = files_mod.resolve_input_path(source)
    final = resolve_target(
        path, suggested_name=os.path.basename(source), content_type=None, overwrite=overwrite
    )
    with AtomicWriter(final, overwrite=overwrite) as writer, open(source, "rb") as handle:
        while True:
            chunk = handle.read(65536)
            if not chunk:
                break
            if max_bytes and writer.bytes + len(chunk) > max_bytes:
                raise BrowserError(
                    f"Файл больше max_bytes={max_bytes}; копирование прервано, частичный файл удалён"
                )
            writer.write(chunk)
        size, sha256 = writer.commit()
    return {
        "path": final,
        "bytes": size,
        "sha256": sha256,
        "content_type": None,
        "url": "file://" + source,
        "status": None,
        "filename_source": "url",
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "free_bytes": disk_free(os.path.dirname(final)),
        "redirects": [],
    }


def op_download(session: BrowserSession, args: dict, ctx: Any) -> dict[str, Any]:
    path = arg_str(args, "path")
    overwrite = arg_bool(args, "overwrite", False)
    url_arg = arg_str(args, "url")
    ref = arg_str(args, "ref")
    tab_arg = arg_str(args, "tab")
    deadline = _deadline(args)
    max_bytes = _non_negative(args, "max_bytes")
    max_redirects = _non_negative(args, "max_redirects")
    use_cookies = arg_bool(args, "use_cookies", True)
    client = session.http()
    notes: list[str] = []
    spec: RequestSpec | None = None
    target_url: str | None = None
    if url_arg:
        target_url, url_notes = _normalize_open_url(url_arg)
        notes.extend(url_notes)
        referrer = None
        if session.current_tab in session.tabs:
            tab = session.tabs[session.current_tab]
            if tab.last_url:
                referrer = referrer_value(tab.last_url, target_url)
        spec = RequestSpec(url=target_url, referrer=referrer)
    elif ref:
        tab = session.get_tab(tab_arg)
        page = current_page(session, tab)
        model = page.model
        if model is None:
            raise _binary_error(tab, page)
        element = model.element(normalize_ref(ref))
        target_url = model.href(element) or model.media_url(element)
        if not target_url:
            raise BrowserNotFound(
                f"У элемента {normalize_ref(ref)} нет адреса для скачивания (href или src); метки: browser_read(view='links')"
            )
        scheme = (urlsplit(target_url).scheme or "").lower()
        if scheme == "blob":
            raise BrowserError(E_BLOB_LINK.format(dir=session.downloads_dir()))
        if scheme not in ("http", "https", "data", "file"):
            raise BrowserError(f"{E_URL_SCHEME}. Метка {normalize_ref(ref)} ведёт на {display_url(target_url)}")
        rel = (element.get("rel") or "").lower().split()
        referrer = referrer_value(page.url, target_url, noreferrer="noreferrer" in rel)
        spec = RequestSpec(url=target_url, referrer=referrer)
    else:
        tab = session.get_tab(tab_arg)
        engine = tab.engine
        if engine is None:
            raise BrowserError(E_TAB_EMPTY.format(tab=tab.id))
        if isinstance(engine, HttpTabEngine) and engine.has_page():
            document = engine.entries[engine.index].doc
            target_url = document.url
            if document.method.upper() == "POST":
                notes.append("запрос POST отправлен повторно")
            spec = replay_spec(document.spec, deadline=deadline)
            target_url = spec.url
        else:
            page = current_page(session, tab)
            target_url = page.url
            spec = RequestSpec(url=target_url)
    assert target_url is not None and spec is not None
    scheme = (urlsplit(target_url).scheme or "").lower()
    if scheme == "data":
        payload, media_type = decode_data_url(target_url)
        if max_bytes and len(payload) > max_bytes:
            raise BrowserError(
                f"Данные больше max_bytes={max_bytes} ({human_bytes(len(payload))}); ничего не сохранено"
            )
        result = _write_bytes(path, payload, media_type.split(";", 1)[0].strip(), overwrite, "data:" + media_type)
    elif scheme == "file":
        result = _copy_local(path, unquote(urlsplit(target_url).path), overwrite, max_bytes)
    else:
        spec.deadline = deadline
        spec.max_bytes = max_bytes
        spec.max_redirects = max_redirects
        spec.use_cookies = use_cookies
        spec.mode = "download"
        result = client.stream_to_file(spec, path, overwrite=overwrite)
    ctx.event(
        "browser_download",
        {
            "url": result["url"],
            "path": result["path"],
            "bytes": result["bytes"],
            "sha256": result["sha256"],
            "elapsed_ms": result["elapsed_ms"],
        },
    )
    data: dict[str, Any] = {"session": session.key}
    data.update(result)
    data["size"] = human_bytes(result["bytes"])
    if notes:
        data["notes"] = notes
    return data


# ---------------------------------------------------------------------------
# browser_session


def _mem_available() -> int | None:
    try:
        with open("/proc/meminfo", encoding="ascii", errors="replace") as handle:
            for line in handle:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def _chrome_lines(session: BrowserSession) -> list[str]:
    browser = session.chrome_if_running()
    if browser is not None:
        try:
            info = browser.info() or {}
        except Exception:
            info = {}
        details = ", ".join(
            f"{key}: {value}" for key, value in info.items() if value not in (None, "", [], {})
        )
        return [f"chrome — запущен ({details})" if details else "chrome — запущен"]
    binary, problem = _chrome_status()
    if binary is not None:
        return [f"chrome — не запущен; найден {getattr(binary, 'path', binary)} (запустится по требованию)"]
    return [f"chrome — недоступен: {problem or 'Chrome/Chromium не найден'}"]


def _status_text(session: BrowserSession) -> str:
    lines = [f"Сессия: {session.describe()}", f"Вкладки: {len(session.tabs)}"]
    for tab in list(session.tabs.values()):
        lines.append("  " + _tab_line(session, tab))
    lines.append("Движки: http — да; " + "; ".join(_chrome_lines(session)))
    client = session.http()
    rows = cookie_rows(client.cookies)
    domains = sorted({str(row.get("domain") or "").lstrip(".") for row in rows})
    lines.append(f"Cookies: {len(rows)}" + (f" (домены: {', '.join(domains)})" if domains else ""))
    lines.append(
        "Сохранённые ответы: " + (", ".join(session.responses) or "нет")
        + "; результаты JavaScript: " + (", ".join(session.evaluations) or "нет")
    )
    if session.chrome_downloads:
        lines.append("Загрузки Chrome:")
        for item in session.chrome_downloads:
            lines.append(
                f"  «{item.get('name')}» → {item.get('path')} ({human_bytes(item.get('bytes'))}, {item.get('state') or 'готово'})"
            )
    lines.append("Настройки:")
    lines.extend("  " + line for line in session.settings.describe())
    workdir = session.workdir_if_created()
    free = disk_free(workdir or os.path.realpath(os.environ.get("TMPDIR") or "/tmp"))
    lines.append(f"Свободно на диске (временные файлы браузера): {human_bytes(free)}")
    memory = _mem_available()
    if memory is not None:
        lines.append(f"Доступная память (MemAvailable): {human_bytes(memory)}")
    return "\n".join(lines)


def _apply_settings(session: BrowserSession, args: Mapping[str, Any], ctx: Any) -> list[str]:
    settings = session.settings
    changed: list[str] = []
    explicit = [name for name in ("mobile", "viewport_width", "viewport_height") if args.get(name) is not None]
    agent = arg_raw_str(args, "user_agent")
    if agent is not None:
        settings.apply_user_agent(agent, explicit=explicit)
        changed.append("user_agent")
    language = arg_str(args, "accept_language")
    if language:
        settings.accept_language = language
        changed.append("accept_language")
    if args.get("headers") is not None:
        pairs = arg_pairs(args, "headers")
        for name, value in pairs:
            _secret_header(ctx, name, value)
        settings.extra_headers = pairs
        changed.append("headers")
    engine = _choice(args, "default_engine", ENGINE_MODES)
    if engine:
        settings.default_engine = engine
        changed.append("default_engine")
    for name in ("viewport_width", "viewport_height"):
        value = arg_int(args, name)
        if value is not None:
            if value < 1:
                raise BrowserError(E_ARG_TYPE.format(name=name, expected="числом от 1", value=value))
            setattr(settings, name, value)
            changed.append(name)
    scale = arg_float(args, "device_scale_factor")
    if scale is not None:
        if scale <= 0:
            raise BrowserError(E_ARG_TYPE.format(name="device_scale_factor", expected="числом больше 0", value=scale))
        settings.device_scale_factor = scale
        changed.append("device_scale_factor")
    for name in ("mobile", "javascript", "load_images"):
        if args.get(name) is not None:
            setattr(settings, name, arg_bool(args, name, getattr(settings, name)))
            changed.append(name)
    dialogs = _choice(args, "dialogs", DIALOG_MODES)
    if dialogs:
        settings.dialogs = dialogs
        changed.append("dialogs")
    if args.get("dialog_prompt_text") is not None:
        settings.dialog_prompt_text = arg_raw_str(args, "dialog_prompt_text")
        changed.append("dialog_prompt_text")
    proxy = arg_raw_str(args, "proxy")
    if proxy is not None:
        value = proxy.strip()
        if value and "://" not in value:
            value = "http://" + value
        password = userinfo_password(value)
        if password:
            ctx.secret(password)
        settings.proxy = value
        changed.append("proxy")
    notes: list[str] = []
    if not changed:
        notes.append("Ничего не изменено: передайте параметры, например user_agent='mobile' или proxy='http://host:port'")
        return notes
    browser = session.chrome_if_running()
    if browser is not None:
        try:
            notes.extend(browser.apply_options(settings.to_chrome()) or [])
        except BrowserError as exc:
            notes.append(f"Chrome не применил настройки: {exc}")
    return notes


def op_session(session: BrowserSession | None, args: dict, ctx: Any) -> dict[str, Any]:
    action = _choice(args, "action", ("list", "status", "configure", "clear", "reset", "close"))
    if action is None:
        raise BrowserError(
            E_ARG_TYPE.format(name="action", expected="list, status, configure, clear, reset или close", value=None)
        )
    key = ctx.session_key
    explicit = arg_str(args, "session") is not None
    max_chars = _non_negative(args, "max_chars")
    offset = _optional_offset(args)
    data: dict[str, Any] = {"action": action}
    if action == "list":
        sessions = list_sessions()
        text = "\n".join(item.describe() for item in sessions) if sessions else "Сессий нет"
        data.update({"session": key, "sessions": len(sessions)})
        data.update(paginate(text, offset, max_chars))
        ctx.event("browser_session", {"action": action, "session": key})
        return data
    if action in ("reset", "close"):
        if arg_bool(args, "all", False):
            targets = list_sessions()
        else:
            found = find_session(key)
            if found is None:
                if explicit:
                    raise BrowserNotFound(E_SESSION_NOT_FOUND.format(id=key))
                targets = []
            else:
                targets = [found]
        done: list[str] = []
        for target in targets:
            acquired = target.lock.acquire(blocking=False)
            if not acquired:
                target.abort()
                target.lock.acquire()
            try:
                if action == "reset":
                    target.reset()
                    target.closed = False
                else:
                    drop_session(target.key)
            finally:
                target.lock.release()
            done.append(target.key)
            ctx.event("browser_session", {"action": action, "session": target.key})
        verb = "сброшены" if action == "reset" else "закрыты"
        text = f"Сессии {verb}: {', '.join(done)}" if done else "Сессия ещё не создана — делать нечего"
        data.update({"session": key, "sessions": done})
        data.update(paginate(text, offset, max_chars))
        return data
    if explicit and action in ("status", "clear"):
        target = find_session(key)
        if target is None:
            raise BrowserNotFound(E_SESSION_NOT_FOUND.format(id=key))
    else:
        target = get_session(key)
    with target.lock:
        if target.closed:
            target = get_session(key)
        notes: list[str] = []
        if action == "configure":
            notes = _apply_settings(target, args, ctx)
            text = "Настройки сессии:\n" + "\n".join(target.settings.describe())
        elif action == "clear":
            counts = target.clear()
            text = (
                f"Освобождено: ответов {counts['responses']}, результатов JavaScript {counts['evaluations']}, "
                f"курсоров поиска {counts['cursors']}, копий страниц {counts['copies']}"
            )
            data["freed"] = counts
        else:
            text = _status_text(target)
        notes = _merge_notes(notes, target.take_notes())
    ctx.event("browser_session", {"action": action, "session": target.key})
    data["session"] = target.key
    data.update(paginate(text, offset, max_chars))
    if notes:
        data["notes"] = notes
    return data


# ---------------------------------------------------------------------------
# Registry


OPERATIONS: dict[str, tuple[Callable[..., dict[str, Any]], bool]] = {
    "browser_open": (op_open, True),
    "browser_read": (op_read, True),
    "browser_find": (op_find, True),
    "browser_extract": (op_extract, True),
    "browser_click": (op_click, True),
    "browser_fill_form": (op_fill_form, True),
    "browser_act": (op_act, True),
    "browser_wait": (op_wait, True),
    "browser_evaluate": (op_evaluate, True),
    "browser_screenshot": (op_screenshot, True),
    "browser_history": (op_history, True),
    "browser_tabs": (op_tabs, True),
    "browser_session": (op_session, False),
    "browser_cookies": (op_cookies, True),
    "browser_search": (op_search, True),
    "browser_request": (op_request, True),
    "browser_download": (op_download, True),
}


__all__ = [
    "OPERATIONS",
    "apply_outcome",
    "challenge_reason",
    "chrome_only_guard",
    "current_page",
    "page_result",
    "view_scope",
]
