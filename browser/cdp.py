"""Chrome DevTools Protocol connection over the websockets synchronous client.

One connection per Chrome process, in flat session mode: commands for a page
or worker carry its ``sessionId``. A daemon reader thread ("logos-browser-cdp")
delivers replies to the waiting caller and events to waiters (``expect``) and
subscribers. Subscribers run on the reader thread, so they only record state
and enqueue work; they never wait for a reply (that would stop the reader).
When the connection closes, every pending call and waiter fails with
``ChromeClosed``.

``websockets`` is imported lazily in ``CdpConnection.__init__``: without it the
browser tools still load and the http engine works.
"""

from __future__ import annotations

import inspect
import itertools
import json
import logging
import threading
from typing import Any, Callable

from browser.errors import (
    E_CHROME_DIED,
    E_WEBSOCKETS,
    BrowserError,
    BrowserTimeout,
    BrowserUnavailable,
    Deadline,
    timeout_message,
)
from browser.redact import redact_text

logger = logging.getLogger("logos.browser.chrome")

Subscriber = Callable[[str, dict, "str | None"], None]


class CdpError(BrowserError):
    """Chrome answered a command with an error."""

    kind = "network"

    def __init__(
        self,
        message: str,
        *,
        method: str = "",
        code: Any = None,
        cdp_message: str = "",
    ) -> None:
        super().__init__(message)
        self.method = method
        self.code = code
        self.cdp_message = cdp_message


class ChromeClosed(BrowserError):
    """The DevTools connection is gone (Chrome exited or was closed)."""

    kind = "network"


class _Pending:
    __slots__ = ("event", "response", "error")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.response: dict[str, Any] | None = None
        self.error: BrowserError | None = None


class Waiter:
    """One expected event; register it (``expect``) before triggering the event."""

    def __init__(
        self,
        connection: "CdpConnection",
        method: str,
        predicate: Callable[[dict], bool] | None,
        session_id: str | None,
    ) -> None:
        self._connection = connection
        self.method = method
        self.predicate = predicate
        self.session_id = session_id
        self.params: dict[str, Any] | None = None
        self.error: BrowserError | None = None
        self._event = threading.Event()

    def _offer(self, method: str, params: dict, session_id: str | None) -> bool:
        if self._event.is_set() or method != self.method:
            return False
        if self.session_id is not None and session_id != self.session_id:
            return False
        if self.predicate is not None:
            try:
                accepted = bool(self.predicate(params))
            except Exception:
                accepted = False
            if not accepted:
                return False
        self.params = params
        self._event.set()
        return True

    def _fail(self, error: BrowserError) -> None:
        if not self._event.is_set():
            self.error = error
            self._event.set()

    @property
    def done(self) -> bool:
        return self._event.is_set()

    def wait(self, deadline: Deadline | None = None, *, what: str | None = None) -> dict[str, Any]:
        """The event's params; BrowserTimeout when the deadline passes first."""
        try:
            remaining = deadline.remaining() if deadline is not None else None
            if not self._event.wait(remaining):
                raise BrowserTimeout(
                    timeout_message(
                        deadline.seconds if deadline is not None else None,
                        what or f"событие Chrome {self.method}",
                    )
                )
        finally:
            self.cancel()
        if self.error is not None:
            raise self.error
        return dict(self.params or {})

    def cancel(self) -> None:
        self._connection._remove_waiter(self)


class CdpConnection:
    """A DevTools WebSocket connection with a reader thread."""

    def __init__(self, ws_url: str, *, deadline: Deadline) -> None:
        try:
            from websockets.sync.client import connect
        except ImportError:
            raise BrowserUnavailable(E_WEBSOCKETS) from None
        kwargs: dict[str, Any] = {
            "max_size": None,
            "compression": None,
            "open_timeout": deadline.remaining(),
        }
        try:
            accepted = inspect.signature(connect).parameters
        except (TypeError, ValueError):
            accepted = {}
        # websockets 13/14 pass unknown keywords on to the socket; give these only when known.
        if "proxy" in accepted:
            kwargs["proxy"] = None
        if "ping_interval" in accepted:
            kwargs["ping_interval"] = None
        try:
            self._ws = connect(ws_url, **kwargs)
        except TimeoutError:
            raise BrowserTimeout(timeout_message(deadline.seconds, "подключение к Chrome")) from None
        except Exception as exc:
            reason = redact_text(f"{type(exc).__name__}: {exc}")
            raise CdpError(f"Не удалось подключиться к Chrome по DevTools: {reason}") from None
        self.alive = True
        self.on_lost: Callable[[str], None] | None = None
        self.closed_reason: str | None = None
        self._ids = itertools.count(1)
        self._state_lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._pending: dict[int, _Pending] = {}
        self._waiters: list[Waiter] = []
        self._subscribers: list[Subscriber] = []
        self._closing = False
        self._finished = False
        self._reader = threading.Thread(target=self._read_loop, name="logos-browser-cdp", daemon=True)
        self._reader.start()

    # -- errors

    def _closed_error(self) -> ChromeClosed:
        return ChromeClosed(E_CHROME_DIED.format(exit=self.closed_reason or "соединение закрыто"))

    # -- sending

    def _send(self, message: dict[str, Any]) -> None:
        # ASCII JSON: lone surrogates in page text cannot break the UTF-8 frame.
        data = json.dumps(message, ensure_ascii=True, separators=(",", ":"))
        with self._send_lock:
            if not self.alive:
                raise self._closed_error()
            try:
                self._ws.send(data)
            except Exception as exc:
                logger.debug("cdp send failed: %s", type(exc).__name__)
                raise self._closed_error() from None

    def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
        deadline: Deadline | None = None,
        what: str | None = None,
    ) -> dict[str, Any]:
        """Send a command and wait for its result; CdpError for an error reply."""
        slot = _Pending()
        with self._state_lock:
            # Checked under the lock _finish takes: a call is either drained or refused.
            if not self.alive:
                raise self._closed_error()
            message_id = next(self._ids)
            self._pending[message_id] = slot
        message: dict[str, Any] = {"id": message_id, "method": method, "params": params or {}}
        if session_id:
            message["sessionId"] = session_id
        try:
            self._send(message)
        except BrowserError:
            with self._state_lock:
                self._pending.pop(message_id, None)
            raise
        remaining = deadline.remaining() if deadline is not None else None
        if not slot.event.wait(remaining):
            with self._state_lock:
                self._pending.pop(message_id, None)
            raise BrowserTimeout(
                timeout_message(
                    deadline.seconds if deadline is not None else None,
                    what or f"ответ Chrome на {method}",
                )
            )
        if slot.error is not None:
            raise slot.error
        response = slot.response or {}
        error = response.get("error")
        if error is not None:
            if isinstance(error, dict):
                text = str(error.get("message") or "ошибка")
                data = error.get("data")
                if data:
                    text = f"{text}: {data}"
                code = error.get("code")
            else:
                text = str(error)
                code = None
            raise CdpError(
                f"Chrome: {redact_text(text)} ({method})",
                method=method,
                code=code,
                cdp_message=text,
            )
        result = response.get("result")
        return result if isinstance(result, dict) else {}

    def send_nowait(
        self, method: str, params: dict[str, Any] | None = None, *, session_id: str | None = None
    ) -> None:
        """Send a command without waiting; its reply is dropped. Never raises."""
        if not self.alive:
            return
        with self._state_lock:
            message_id = next(self._ids)
        message: dict[str, Any] = {"id": message_id, "method": method, "params": params or {}}
        if session_id:
            message["sessionId"] = session_id
        try:
            self._send(message)
        except BrowserError:
            logger.debug("cdp %s not sent: connection closed", method)

    # -- events

    def subscribe(self, callback: Subscriber) -> None:
        """Call ``callback(method, params, session_id)`` for every event (on the reader thread)."""
        with self._state_lock:
            self._subscribers.append(callback)

    def expect(
        self,
        method: str,
        predicate: Callable[[dict], bool] | None = None,
        *,
        session_id: str | None = None,
    ) -> Waiter:
        """A waiter for the next matching event; register it before triggering the event."""
        waiter = Waiter(self, method, predicate, session_id)
        with self._state_lock:
            if not self.alive:
                waiter._fail(self._closed_error())
            else:
                self._waiters.append(waiter)
        return waiter

    def _remove_waiter(self, waiter: Waiter) -> None:
        with self._state_lock:
            try:
                self._waiters.remove(waiter)
            except ValueError:
                pass

    def _dispatch(self, method: str, params: dict, session_id: str | None) -> None:
        with self._state_lock:
            waiters = list(self._waiters)
            subscribers = list(self._subscribers)
        matched = [waiter for waiter in waiters if waiter._offer(method, params, session_id)]
        if matched:
            with self._state_lock:
                for waiter in matched:
                    try:
                        self._waiters.remove(waiter)
                    except ValueError:
                        pass
        for callback in subscribers:
            try:
                callback(method, params, session_id)
            except Exception as exc:
                logger.debug("cdp subscriber failed on %s: %s", method, type(exc).__name__)

    # -- reader

    def _read_loop(self) -> None:
        reason = "соединение DevTools закрыто"
        try:
            while True:
                raw = self._ws.recv()
                if isinstance(raw, (bytes, bytearray)):
                    raw = bytes(raw).decode("utf-8", errors="replace")
                try:
                    message = json.loads(raw)
                except ValueError:
                    logger.debug("cdp message is not JSON")
                    continue
                if not isinstance(message, dict):
                    continue
                if "id" in message and "method" not in message:
                    with self._state_lock:
                        slot = self._pending.pop(message.get("id"), None)
                    if slot is not None:
                        slot.response = message
                        slot.event.set()
                    continue
                method = message.get("method")
                if not isinstance(method, str):
                    continue
                params = message.get("params")
                session_id = message.get("sessionId")
                self._dispatch(
                    method,
                    params if isinstance(params, dict) else {},
                    session_id if isinstance(session_id, str) else None,
                )
        except Exception as exc:
            if not self._closing:
                reason = f"соединение DevTools оборвалось ({type(exc).__name__})"
                logger.debug("cdp reader stopped: %s", type(exc).__name__)
        finally:
            self._finish(reason)

    def _finish(self, reason: str) -> None:
        with self._state_lock:
            if self._finished:
                return
            self._finished = True
            self.alive = False
            if self.closed_reason is None:
                self.closed_reason = reason
            pending = list(self._pending.values())
            self._pending.clear()
            waiters = list(self._waiters)
            self._waiters.clear()
        for slot in pending:
            slot.error = self._closed_error()
            slot.event.set()
        for waiter in waiters:
            waiter._fail(self._closed_error())
        callback = self.on_lost
        if callback is not None and not self._closing:
            try:
                callback(self.closed_reason or reason)
            except Exception as exc:
                logger.debug("cdp on_lost failed: %s", type(exc).__name__)

    def close(self, reason: str | None = None) -> None:
        """Close the connection; pending calls fail with ChromeClosed."""
        with self._state_lock:
            if self._closing:
                return
            self._closing = True
            if reason and self.closed_reason is None:
                self.closed_reason = reason
        try:
            self._ws.close()
        except Exception as exc:
            logger.debug("cdp close failed: %s", type(exc).__name__)
        self._finish(reason or "соединение DevTools закрыто")


__all__ = ["CdpConnection", "CdpError", "ChromeClosed", "Waiter"]
