"""Browser sessions: tabs, engines, cookies and the Chrome lifecycle of one dialog.

A session belongs to one dialog (or to an explicit ``session`` name; MCP calls
share "default"). It keeps its tabs, the HTTP client with its cookie jar, a
Chrome process started only when a chrome tab needs it, stored responses and
JavaScript results, and the settings. Every operation runs under the session
lock; ``abort`` is the one lock-free entry, used to stop a running operation
before a reset or close.

Cookies have one owner at a time: the HTTP jar or Chrome. Switching engines
moves them over (Storage.getCookies into the jar, or the jar into Chrome with
replace semantics), so both engines see one cookie store.
"""

from __future__ import annotations

import atexit
import logging
import os
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from browser.engine import HistoryItem, Hop, TabEngine
from browser.errors import (
    E_ENGINE_UNKNOWN,
    E_NO_TABS,
    E_SESSION_CLOSED,
    E_TAB_NOT_FOUND,
    BrowserError,
    BrowserNotFound,
    BrowserUnavailable,
    Deadline,
    chrome_missing_message,
)
from browser.http_engine import HttpClient, cdp_to_jar, jar_to_cdp
from browser.http_tab import HttpTabEngine
from browser.settings import ENGINE_MODES, SessionSettings

logger = logging.getLogger("logos.browser.sessions")

_SESSIONS: dict[str, "BrowserSession"] = {}
_SESSIONS_LOCK = threading.Lock()
# Process-wide one-time steps: the exit hook and the orphan sweep of old Chrome profiles.
_PROCESS_STATE: dict[str, bool] = {"atexit": False, "swept": False}


@dataclass
class Tab:
    id: str
    mode: str
    engine: TabEngine | None = None
    previous: list[HistoryItem] = field(default_factory=list)
    opener: str | None = None
    created_at: float = field(default_factory=time.time)
    lost_reason: str | None = None
    last_url: str = ""
    last_title: str = ""


@dataclass
class StoredResponse:
    id: str
    method: str
    url: str
    status: int | None
    reason: str
    http_version: str | None
    headers_masked: list[tuple[str, str]]
    hops: list[Hop]
    content_type: str
    charset: str
    kind: str
    text: str
    bytes: int | None
    elapsed_ms: int | None
    notes: list[str] = field(default_factory=list)


class _NullSink:
    """EventSink for lifecycle steps that run without a tool call (exit, close)."""

    def event(self, event_type: str, payload: Mapping[str, Any]) -> None:
        return None

    def secret(self, value: str | None) -> None:
        return None


_NULL_SINK = _NullSink()


def _iso(timestamp: float | None) -> str:
    if not timestamp:
        return "—"
    return datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _alive(browser: Any) -> bool:
    try:
        return bool(browser.alive())
    except Exception:
        return False


def _register_exit_hook() -> None:
    if _PROCESS_STATE["atexit"]:
        return
    _PROCESS_STATE["atexit"] = True
    atexit.register(close_all)


def _tab_key(tab_id: str) -> str:
    text = str(tab_id).strip().lower()
    if text.isdigit():
        return f"t{text}"
    return text


class BrowserSession:
    """Tabs, engines, cookies and stored results of one dialog's browser."""

    def __init__(self, key: str) -> None:
        self.key = key
        self.lock = threading.RLock()
        self.settings = SessionSettings()
        self.tabs: dict[str, Tab] = {}
        self.current_tab: str | None = None
        self.responses: dict[str, StoredResponse] = {}
        self.evaluations: dict[str, str] = {}
        self.search_cursors: dict = {}
        self.cookie_owner = "http"
        self.created_at = time.time()
        self.used_at = self.created_at
        self.closed = False
        self.chrome_downloads: list[dict] = []
        self._http: HttpClient | None = None
        self._chrome: Any = None
        self._workdir: str | None = None
        self._tab_counter = 0
        self._response_counter = 0
        self._eval_counter = 0
        self._notes: list[str] = []

    # -- bookkeeping

    def touch(self) -> None:
        self.used_at = time.time()

    def add_note(self, note: str) -> None:
        if note and note not in self._notes:
            self._notes.append(note)

    def take_notes(self) -> list[str]:
        notes, self._notes = self._notes, []
        return notes

    def workdir(self) -> str:
        """The session's private temporary directory (Chrome profile, downloads)."""
        if self._workdir is None or not os.path.isdir(self._workdir):
            self._workdir = tempfile.mkdtemp(prefix="logos-browser-")
        return self._workdir

    def workdir_if_created(self) -> str | None:
        """The temporary directory when it exists (status reports must not create one)."""
        if self._workdir and os.path.isdir(self._workdir):
            return self._workdir
        return None

    def downloads_dir(self) -> str:
        browser = self._chrome
        path = getattr(browser, "downloads_dir", None) if browser is not None else None
        return str(path) if path else os.path.join(self.workdir(), "downloads")

    # -- HTTP

    def http(self) -> HttpClient:
        """The HTTP client; cookies owned by a running Chrome are pulled into its jar first."""
        if self._http is None:
            self._http = HttpClient(lambda: self.settings)
        if self.cookie_owner == "chrome":
            browser = self._chrome
            if browser is not None and _alive(browser):
                try:
                    cdp_to_jar(self._http.cookies, browser.get_cookies())
                except BrowserError as exc:
                    logger.debug("cookie pull from chrome failed: %s", type(exc).__name__)
                    self.add_note(f"Не удалось забрать cookies из Chrome: {exc}")
            self.cookie_owner = "http"
        return self._http

    # -- Chrome

    def chrome_if_running(self, ctx: Any = None) -> Any:
        """The live Chrome of this session, or None (a dead one is marked lost)."""
        browser = self._chrome
        if browser is None:
            return None
        if _alive(browser):
            return browser
        self._chrome_lost(browser, ctx or _NULL_SINK)
        return None

    def _chrome_lost(self, browser: Any, ctx: Any) -> list[str]:
        try:
            exit_text = str(browser.exit_info())
        except Exception:
            exit_text = "причина неизвестна"
        lost: list[str] = []
        for tab in self.tabs.values():
            if tab.engine is not None and tab.engine.name == "chrome":
                tab.lost_reason = f"Chrome завершился: {exit_text}"
                try:
                    tab.engine.lost = True
                except AttributeError:
                    pass
                lost.append(tab.id)
        ctx.event(
            "browser_chrome_lost",
            {"pid": getattr(browser, "pid", None), "exit": exit_text, "tabs": lost},
        )
        try:
            browser.close("Chrome завершился неожиданно")
        except Exception as exc:
            logger.debug("closing a dead chrome failed: %s", type(exc).__name__)
        self._chrome = None
        if self.cookie_owner == "chrome":
            # Chrome's cookies are gone with it; the jar is the store again.
            self.cookie_owner = "http"
        logger.debug("chrome of session %s lost (%s), tabs %s", self.key, exit_text, lost)
        return lost

    def chrome(self, action: str, *, deadline: Deadline, ctx: Any) -> Any:
        """The running Chrome of this session, launched (or relaunched) when needed."""
        from browser import chrome_launch
        from browser.chrome import ChromeBrowser

        browser = self._chrome
        lost: list[str] = []
        if browser is not None and not _alive(browser):
            lost = self._chrome_lost(browser, ctx)
            browser = None
        if browser is None:
            binary, problem = chrome_launch.chrome_status()
            if binary is None:
                raise BrowserUnavailable(chrome_missing_message(action, problem))
            swept = 0
            if not _PROCESS_STATE["swept"]:
                _PROCESS_STATE["swept"] = True
                try:
                    swept = len(chrome_launch.sweep_orphans() or [])
                except Exception as exc:
                    logger.debug("orphan sweep failed: %s", type(exc).__name__)
            browser = ChromeBrowser.launch(
                binary=binary,
                options=self.settings.to_chrome(),
                workdir=self.workdir(),
                deadline=deadline,
            )
            self._chrome = browser
            _register_exit_hook()
            ctx.event(
                "browser_chrome_started",
                {
                    "binary": getattr(binary, "path", str(binary)),
                    "version": getattr(browser, "version", None),
                    "pid": getattr(browser, "pid", None),
                    "swept": swept,
                },
            )
            logger.debug("chrome started for session %s, pid %s", self.key, getattr(browser, "pid", None))
            if lost:
                self.add_note(f"Chrome был перезапущен; вкладки chrome потеряны: {', '.join(lost)}")
            if self.cookie_owner == "chrome":
                self.cookie_owner = "http"
        if self.cookie_owner == "http":
            if self._http is not None:
                browser.set_cookies(jar_to_cdp(self._http.cookies))
            self.cookie_owner = "chrome"
        self.drain_chrome(ctx)
        return browser

    def drain_chrome(self, ctx: Any) -> list[dict]:
        """Forward Chrome's queued trace events and collect finished downloads (new ones)."""
        browser = self._chrome
        if browser is None:
            return []
        try:
            events = browser.take_events() or []
        except Exception:
            events = []
        for event_type, payload in events:
            ctx.event(event_type, payload)
        try:
            downloads = browser.take_downloads() or []
        except Exception:
            downloads = []
        return self.record_downloads(downloads)

    def record_downloads(self, downloads: list[dict]) -> list[dict]:
        """Remember Chrome downloads; returns the ones not seen before."""
        known = {str(item.get("path")) for item in self.chrome_downloads}
        fresh: list[dict] = []
        for item in downloads or []:
            if not isinstance(item, dict):
                continue
            path = str(item.get("path"))
            if path in known:
                continue
            known.add(path)
            self.chrome_downloads.append(dict(item))
            fresh.append(dict(item))
        return fresh

    def stop_chrome(self, reason: str, ctx: Any = None) -> None:
        """Close Chrome after moving its cookies into the jar."""
        browser = self._chrome
        if browser is None:
            return
        sink = ctx or _NULL_SINK
        if self.cookie_owner == "chrome":
            self.http()
        self.drain_chrome(sink)
        pid = getattr(browser, "pid", None)
        try:
            browser.close(reason)
        except Exception as exc:
            logger.debug("chrome close failed: %s", type(exc).__name__)
        self._chrome = None
        self.cookie_owner = "http"
        sink.event("browser_chrome_stopped", {"pid": pid, "reason": reason})

    # -- tabs

    def get_tab(self, tab_id: str | None) -> Tab:
        """The named tab, or the current one for None."""
        if tab_id is None or not str(tab_id).strip():
            if not self.tabs or self.current_tab not in self.tabs:
                raise BrowserError(E_NO_TABS)
            return self.tabs[self.current_tab]
        key = _tab_key(str(tab_id))
        tab = self.tabs.get(key)
        if tab is None:
            raise BrowserNotFound(
                E_TAB_NOT_FOUND.format(id=str(tab_id).strip(), ids=", ".join(self.tabs) or "нет")
            )
        return tab

    def new_tab(self, *, mode: str | None, opener: str | None = None, make_current: bool = True) -> Tab:
        chosen = mode or self.settings.default_engine
        if chosen not in ENGINE_MODES:
            raise BrowserError(E_ENGINE_UNKNOWN.format(engine=chosen), kind="engine")
        self._tab_counter += 1
        tab_id = f"t{self._tab_counter}"
        while tab_id in self.tabs:
            self._tab_counter += 1
            tab_id = f"t{self._tab_counter}"
        tab = Tab(id=tab_id, mode=chosen, opener=opener)
        self.tabs[tab_id] = tab
        if make_current or self.current_tab is None:
            self.current_tab = tab_id
        return tab

    def make_engine(
        self, tab: Tab, kind: str, *, deadline: Deadline, ctx: Any, action: str = "browser_open"
    ) -> TabEngine:
        """A new engine for ``tab`` without attaching it (the tab keeps its engine)."""
        if kind == "http":
            return HttpTabEngine(self.http(), tab.id)
        browser = self.chrome(action, deadline=deadline, ctx=ctx)
        return browser.new_tab(tab.id, deadline=deadline)

    def attach_engine(self, tab: Tab, engine: TabEngine, *, ctx: Any, reason: str = "") -> None:
        """Make ``engine`` the tab's engine; the old one's history is kept in tab.previous."""
        old = tab.engine
        if old is engine:
            tab.lost_reason = None
            return
        if old is not None:
            try:
                items = old.history()
            except Exception:
                items = []
            if items:
                tab.previous.extend(items)
            try:
                old.close()
            except Exception as exc:
                logger.debug("closing old engine of %s failed: %s", tab.id, type(exc).__name__)
        tab.engine = engine
        tab.lost_reason = None
        ctx.event(
            "browser_engine_switch",
            {
                "tab": tab.id,
                "from": old.name if old is not None else "none",
                "to": engine.name,
                "reason": reason or "",
            },
        )

    def engine_for(
        self,
        tab: Tab,
        kind: str,
        *,
        deadline: Deadline,
        ctx: Any,
        action: str = "browser_open",
        reason: str = "",
    ) -> TabEngine:
        """The tab's engine of ``kind``, switching engines when the tab has another one."""
        current = tab.engine
        if (
            current is not None
            and current.name == kind
            and not getattr(current, "lost", False)
            and tab.lost_reason is None
        ):
            if kind == "chrome":
                # Keeps the cookie owner in step and relaunch detection current.
                self.chrome(action, deadline=deadline, ctx=ctx)
                if tab.lost_reason is None and not getattr(current, "lost", False):
                    return current
            else:
                return current
        engine = self.make_engine(tab, kind, deadline=deadline, ctx=ctx, action=action)
        self.attach_engine(tab, engine, ctx=ctx, reason=reason)
        return engine

    def adopt_popups(self, engines: list[TabEngine]) -> list[str]:
        """Tabs for windows a page opened; the newest becomes current."""
        ids: list[str] = []
        opener = self.current_tab
        opener_tab = self.tabs.get(opener) if opener else None
        mode = opener_tab.mode if opener_tab is not None else "chrome"
        for engine in engines or []:
            tab = self.new_tab(mode=mode, opener=opener, make_current=False)
            try:
                engine.tab_id = tab.id
            except AttributeError:
                pass
            tab.engine = engine
            ids.append(tab.id)
        if ids:
            self.current_tab = ids[-1]
        return ids

    def close_tab(self, tab_id: str | None, ctx: Any = None) -> str | None:
        """Close a tab; the last remaining tab becomes current. Returns the current tab id."""
        tab = self.get_tab(tab_id)
        engine = tab.engine
        was_chrome = engine is not None and engine.name == "chrome"
        if engine is not None:
            try:
                engine.close()
            except Exception as exc:
                logger.debug("closing tab %s failed: %s", tab.id, type(exc).__name__)
        del self.tabs[tab.id]
        if self.current_tab == tab.id or self.current_tab not in self.tabs:
            self.current_tab = next(reversed(self.tabs), None) if self.tabs else None
        if was_chrome and self._chrome is not None:
            still = any(
                other.engine is not None and other.engine.name == "chrome"
                for other in self.tabs.values()
            )
            if not still:
                self.stop_chrome("закрыта последняя вкладка chrome", ctx)
        return self.current_tab

    # -- stored results

    def store_response(self, response: StoredResponse) -> str:
        self._response_counter += 1
        response_id = f"R{self._response_counter}"
        response.id = response_id
        self.responses[response_id] = response
        return response_id

    def store_evaluation(self, text: str) -> str:
        self._eval_counter += 1
        result_id = f"E{self._eval_counter}"
        self.evaluations[result_id] = text
        return result_id

    # -- maintenance

    def clear(self) -> dict:
        """Free memory: stored responses and results, search cursors, page copies."""
        counts = {
            "responses": len(self.responses),
            "evaluations": len(self.evaluations),
            "cursors": len(self.search_cursors),
            "copies": 0,
        }
        self.responses.clear()
        self.evaluations.clear()
        self.search_cursors.clear()
        for tab in self.tabs.values():
            engine = tab.engine
            if isinstance(engine, HttpTabEngine):
                counts["copies"] += engine.drop_copies()
        return counts

    def _close_resources(self, reason: str) -> None:
        browser = self._chrome
        self._chrome = None
        if browser is not None:
            try:
                browser.close(reason)
            except Exception as exc:
                logger.debug("chrome close failed: %s", type(exc).__name__)
        for tab in list(self.tabs.values()):
            engine = tab.engine
            if engine is not None and engine.name == "http":
                try:
                    engine.close()
                except Exception:
                    pass
        client = self._http
        self._http = None
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
        workdir = self._workdir
        self._workdir = None
        if workdir:
            shutil.rmtree(workdir, ignore_errors=True)

    def reset(self) -> None:
        """Close everything and start from a fresh profile; settings are kept."""
        self._close_resources("сброс сессии")
        self.tabs.clear()
        self.current_tab = None
        self._tab_counter = 0
        self.responses.clear()
        self.evaluations.clear()
        self.search_cursors.clear()
        self.chrome_downloads.clear()
        self.cookie_owner = "http"
        self._notes = []
        self.touch()
        logger.debug("session %s reset", self.key)

    def shutdown(self, reason: str = "сессия закрыта") -> None:
        self.closed = True
        self._close_resources(reason)
        self.tabs.clear()
        self.current_tab = None
        logger.debug("session %s closed", self.key)

    def abort(self) -> None:
        """Stop a running operation from another thread: no lock is taken."""
        self.closed = True
        client = self._http
        if client is not None:
            try:
                client.abort()
            except Exception:
                pass
        browser = self._chrome
        if browser is not None:
            try:
                browser.close("сессия прервана")
            except Exception:
                pass

    def describe(self) -> str:
        browser = self._chrome
        pid = getattr(browser, "pid", None) if browser is not None and _alive(browser) else None
        current = f", текущая {self.current_tab}" if self.current_tab else ""
        chrome_text = f", Chrome pid {pid}" if pid else ", Chrome не запущен"
        return (
            f"{self.key}: вкладок {len(self.tabs)}{current}{chrome_text}, "
            f"создана {_iso(self.created_at)}, использована {_iso(self.used_at)}"
        )


# ---------------------------------------------------------------------------
# Session store


def get_session(key: str) -> BrowserSession:
    with _SESSIONS_LOCK:
        session = _SESSIONS.get(key)
        if session is None:
            session = BrowserSession(key)
            _SESSIONS[key] = session
        return session


def find_session(key: str) -> BrowserSession | None:
    with _SESSIONS_LOCK:
        return _SESSIONS.get(key)


def list_sessions() -> list[BrowserSession]:
    with _SESSIONS_LOCK:
        return list(_SESSIONS.values())


def drop_session(key: str) -> bool:
    with _SESSIONS_LOCK:
        session = _SESSIONS.pop(key, None)
    if session is None:
        return False
    session.shutdown()
    return True


def close_all() -> None:
    """Close every session (process exit): Chrome processes stop, profiles are removed."""
    with _SESSIONS_LOCK:
        sessions = list(_SESSIONS.values())
        _SESSIONS.clear()
    for session in sessions:
        try:
            session.abort()
            session.shutdown("процесс Logos завершается")
        except Exception as exc:
            logger.debug("closing session %s failed: %s", session.key, type(exc).__name__)


def run_operation(ctx: Any, operation: Callable[[BrowserSession, dict, Any], dict], args: dict) -> dict:
    """Run one tool operation under the session lock of the caller's session."""
    session = get_session(ctx.session_key)
    with session.lock:
        if session.closed:
            raise BrowserError(E_SESSION_CLOSED.format(key=session.key))
        session.touch()
        try:
            result = operation(session, args, ctx)
        except BrowserError as exc:
            if session.closed and exc.kind in ("network", "timeout"):
                raise BrowserError(E_SESSION_CLOSED.format(key=session.key)) from None
            raise
        except Exception:
            if session.closed:
                raise BrowserError(E_SESSION_CLOSED.format(key=session.key)) from None
            raise
        notes = session.take_notes()
        if notes and isinstance(result, dict):
            existing = [note for note in result.get("notes") or [] if note not in notes]
            result["notes"] = notes + existing
        return result


__all__ = [
    "BrowserSession",
    "StoredResponse",
    "Tab",
    "close_all",
    "drop_session",
    "find_session",
    "get_session",
    "list_sessions",
    "run_operation",
]
