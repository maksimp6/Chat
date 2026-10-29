"""Live chat turns: the event log a browser reads as server-sent events while a turn
runs, and the durable receipt that makes a turn re-sent with the same request_id run
only once.

The turn itself runs in a worker thread (chat.mcp_routes); this module only records
what happens and relays it. A reader that disconnects never stops the turn.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
import uuid
from typing import Any

from flask import Response, jsonify, stream_with_context

from chat._error_details import error_body
from chat.host import check_access, get_current_owner_id, get_store

logger = logging.getLogger("logos.chat.live_turns")

# A reader that hears nothing for this long gets a comment, so proxies and the phone
# keep the connection open.
KEEPALIVE_SECONDS = 15
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
REQUEST_ID_MESSAGE = (
    "request_id must be 16-128 characters: latin letters, digits, _ or -"
)
# This server process in turn receipts: a receipt of another process whose turn did
# not finish was cut by a restart.
PROCESS_TOKEN = uuid.uuid4().hex

_STATUS_TEXTS = {
    "sending": "Отправляем вопрос модели",
    "queued": "Запрос в очереди у провайдера",
    "generating": "Модель отвечает",
    "reasoning": "Модель размышляет",
    "writing": "Модель пишет ответ",
    "tool_round": "Модель продолжает после инструментов",
    "resuming": "Связь с провайдером прервалась: подключаюсь снова",
    "polling": "Жду готовый ответ провайдера",
    "finalizing": "Сохраняю ответ",
    "stopping": "Останавливаю ход: новые запросы к модели и инструменты не запускаются",
}


def _named(prefix: str, name: Any) -> str:
    return f"{prefix} {name}" if name else prefix


def tool_text(kind: str, phase: str, name: Any, server_label: Any) -> str:
    """The status line for a tool event; a missing name or server label is left out."""
    if kind == "web_search":
        return {
            "done": "Поиск в интернете завершён",
            "failed": "Поиск в интернете не удался",
        }.get(phase, "Ищу в интернете")
    if kind == "file_search":
        return {
            "done": "Поиск в базе знаний завершён",
            "failed": "Поиск в базе знаний не удался",
        }.get(phase, "Ищу в базе знаний")
    if kind == "code_interpreter":
        return {
            "done": "Код выполнен",
            "failed": "Код завершился с ошибкой",
        }.get(phase, "Выполняю код")
    if kind == "image_generation":
        return {
            "done": "Изображение создано",
            "failed": "Изображение не создано",
        }.get(phase, "Создаю изображение")
    if kind == "mcp":
        if phase == "done":
            return _named("MCP-инструмент", name) + " выполнен"
        if phase == "failed":
            return _named("MCP-инструмент", name) + " завершился с ошибкой"
        if name and server_label:
            return f"Вызываю {name} на MCP-сервере {server_label}"
        if name:
            return f"Вызываю {name}"
        if server_label:
            return f"Вызываю инструмент на MCP-сервере {server_label}"
        return "Вызываю MCP-инструмент"
    if kind == "mcp_list_tools":
        if phase == "done":
            return _named("Список инструментов MCP-сервера", server_label) + " получен"
        if phase == "failed":
            return _named("Список инструментов MCP-сервера", server_label) + " не получен"
        return _named("Получаю список инструментов MCP-сервера", server_label)
    # function, custom and anything the provider adds later
    if phase == "requested":
        return _named("Модель вызывает инструмент", name)
    if phase == "done":
        return _named("Инструмент", name) + " выполнен"
    if phase == "failed":
        return _named("Инструмент", name) + " завершился с ошибкой"
    return _named("Выполняю инструмент", name)


def reason_text(reason: Any) -> str:
    """The Russian name of a retry reason code."""
    reason = str(reason or "")
    if reason.startswith("connect_"):
        return "Нет связи с провайдером"
    if reason == "http_429":
        return "Провайдер ограничил частоту запросов (HTTP 429)"
    if reason == "http_503":
        return "Провайдер временно недоступен (HTTP 503)"
    if reason.startswith("http_"):
        code = reason[len("http_") :]
        if code.isdigit() and int(code) >= 500:
            return f"Ошибка на стороне провайдера (HTTP {code})"
        return f"Провайдер не принял запрос (HTTP {code})"
    if reason == "not_found":
        return "Ответ ещё не появился у провайдера"
    if reason == "invalid_json":
        return "Провайдер прислал нечитаемый ответ"
    if reason == "stream_interrupted":
        return "Поток ответа прервался"
    if reason == "provider_error_event":
        return "Провайдер сообщил о временной ошибке"
    return "Связь с провайдером прервалась"


def retry_text(operation: Any, reason: Any, delay_s: float, attempt: int) -> str:
    reason_ru = reason_text(reason)
    if operation == "conversation":
        reason_ru += " при создании диалога"
    seconds = f"{max(0.0, delay_s):.1f}".replace(".", ",")
    return f"{reason_ru}: повтор через {seconds} с (попытка {attempt})"


def _number(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(value)


def _merged(events: list[dict[str, Any]], offset: int):
    """(event index, event) pairs; a run of deltas, or of reasoning with one summary
    value, becomes one event that carries the index of its last part."""
    i = 0
    while i < len(events):
        event = events[i]
        j = i
        event_type = event.get("type")
        if event_type in ("delta", "reasoning"):
            texts = [event.get("text") or ""]
            while j + 1 < len(events):
                following = events[j + 1]
                if following.get("type") != event_type:
                    break
                if event_type == "reasoning" and bool(following.get("summary")) != bool(
                    event.get("summary")
                ):
                    break
                j += 1
                texts.append(following.get("text") or "")
            if j > i:
                event = {**event, "text": "".join(texts)}
        yield offset + j + 1, event
        i = j + 1


class LiveTurn:
    """The event log of one chat turn and the translation of provider progress into it."""

    def __init__(self, conversation_id: Any, request_id: str, owner_id: Any):
        self.conversation_id = conversation_id
        self.request_id = request_id
        self.owner_id = owner_id
        self.fingerprint: str | None = None
        self.events: list[dict[str, Any]] = []
        self.condition = threading.Condition(threading.RLock())
        self.accepted = False
        self.finished = False
        self.user_message_id: Any = None
        self.invocation_id: Any = None
        # Translator state: the text and reasoning of the current provider request.
        self._round_text = ""
        self._round_reasoning = ""
        self._reasoning_part: Any = None
        self._phase: str | None = None
        # «Стоп»: the turn starts no new provider request or tool call once this is set,
        # and the provider response in flight (the last one heard of) is cancelled.
        self.stop_requested = threading.Event()
        self.response_id: str | None = None

    def emit(self, event: dict[str, Any]) -> None:
        with self.condition:
            self.events.append(event)
            self.condition.notify_all()

    def _status(self, phase: str, text: str, *, force: bool = False) -> None:
        if not force and phase == self._phase:
            return
        self._phase = phase
        self.emit({"type": "status", "phase": phase, "text": text})

    def accept(self, *, user_message_id: Any, invocation: Any, title: Any, model: Any) -> None:
        """The question is stored: from here on the browser never posts this turn again."""
        with self.condition:
            self.user_message_id = user_message_id
            self.invocation_id = invocation.invocation_id
            self.events.append(
                {
                    "type": "turn",
                    "request_id": self.request_id,
                    "conversation_id": invocation.conversation_id,
                    "user_message_id": user_message_id,
                    "invocation_id": invocation.invocation_id,
                    "session_id": invocation.session_id,
                    "trace_id": invocation.trace_id,
                    "title": title if isinstance(title, str) else None,
                    "model": model,
                }
            )
            self.accepted = True
            self.condition.notify_all()
            self._status("sending", _STATUS_TEXTS["sending"])

    def request_stop(self) -> str | None:
        """Mark the turn stopped; the provider response in flight, if one is known."""
        with self.condition:
            self.stop_requested.set()
            self._status("stopping", _STATUS_TEXTS["stopping"], force=True)
            return self.response_id

    def provider_event(self, event: dict[str, Any]) -> None:
        """Translate one normalized progress event into the wire events; never raises."""
        try:
            with self.condition:
                response_id = event.get("response_id") if isinstance(event, dict) else None
                if isinstance(response_id, str) and response_id:
                    self.response_id = response_id
                self._translate(event)
        except Exception:
            logger.exception(
                "Live event %s was not relayed",
                event.get("kind") if isinstance(event, dict) else type(event).__name__,
            )

    def _translate(self, event: dict[str, Any]) -> None:
        kind = event.get("kind")
        if kind == "round":
            round_kind = event.get("round_kind") or "initial"
            part = int(_number(event.get("part"))) if round_kind == "output_limit" else 0
            self._round_text = ""
            self._round_reasoning = ""
            self._reasoning_part = None
            self.emit(
                {
                    "type": "round",
                    "round": max(1, int(_number(event.get("round"), 1))),
                    "kind": round_kind,
                    "part": part,
                }
            )
            if round_kind == "tool":
                self._status("sending", _STATUS_TEXTS["tool_round"], force=True)
            elif round_kind == "output_limit":
                self._status(
                    "continuation", f"Ответ длинный: продолжаю, часть {part}", force=True
                )
            return
        if kind == "round_result":
            text = str(event.get("text") or "")
            reasoning = str(event.get("reasoning") or "")
            if text != self._round_text or reasoning != self._round_reasoning:
                self.emit({"type": "round_text", "text": text, "reasoning": reasoning})
            self._round_text = text
            self._round_reasoning = reasoning
            return
        if kind == "text_delta":
            text = event.get("text")
            if not isinstance(text, str) or not text:
                return
            self._status("writing", _STATUS_TEXTS["writing"])
            self._round_text += text
            self.emit({"type": "delta", "text": text})
            return
        if kind == "reasoning_delta":
            text = event.get("text")
            if not isinstance(text, str) or not text:
                return
            self._status("reasoning", _STATUS_TEXTS["reasoning"])
            part = event.get("part")
            if part != self._reasoning_part:
                if self._round_reasoning:
                    text = "\n\n" + text
                self._reasoning_part = part
            self._round_reasoning += text
            self.emit(
                {"type": "reasoning", "text": text, "summary": bool(event.get("summary"))}
            )
            return
        if kind == "response_status":
            status = event.get("status")
            if status == "queued":
                self._status("queued", _STATUS_TEXTS["queued"])
            elif status == "in_progress":
                self._status("generating", _STATUS_TEXTS["generating"])
            return
        if kind in ("tool_progress", "tool_call"):
            tool = (event.get("tool") or "custom") if kind == "tool_progress" else "function"
            phase = event.get("phase") or "start"
            name = event.get("name")
            server_label = event.get("server_label")
            # The next status is shown again after a tool line.
            self._phase = "tool"
            self.emit(
                {
                    "type": "tool",
                    "phase": phase,
                    "kind": tool,
                    "name": name if isinstance(name, str) and name else None,
                    "server_label": server_label
                    if isinstance(server_label, str) and server_label
                    else None,
                    "text": tool_text(tool, phase, name, server_label),
                }
            )
            return
        if kind == "retry":
            delay_s = _number(event.get("delay_s"))
            attempt = max(1, int(_number(event.get("attempt"), 1)))
            status_code = event.get("status_code")
            self._phase = "retry"
            self.emit(
                {
                    "type": "retry",
                    "operation": event.get("operation"),
                    "attempt": attempt,
                    "reason": event.get("reason"),
                    "status_code": status_code
                    if isinstance(status_code, int) and not isinstance(status_code, bool)
                    else None,
                    "delay_ms": int(round(delay_s * 1000)),
                    "text": retry_text(
                        event.get("operation"), event.get("reason"), delay_s, attempt
                    ),
                }
            )
            return
        if kind == "stream_interrupted":
            self._status("resuming", _STATUS_TEXTS["resuming"])
            return
        if kind == "stream_resumed":
            if event.get("mode") == "poll":
                self._status("polling", _STATUS_TEXTS["polling"])
            else:
                self._status("generating", _STATUS_TEXTS["generating"])
            return
        if kind == "polling":
            self._status("polling", _STATUS_TEXTS["polling"])
            return
        if kind == "finalizing":
            self._status("finalizing", _STATUS_TEXTS["finalizing"])

    def finish(self, http_status: int, body: Any) -> None:
        """The turn's last event: the body the JSON path would have answered with."""
        with self.condition:
            if self.finished:
                return
            self.events.append(
                {
                    "type": "done",
                    "http_status": int(http_status),
                    "body": body if isinstance(body, dict) else {},
                }
            )
            self.finished = True
            self.condition.notify_all()
        # Readers keep their own reference and still read up to done.
        unregister(self)

    def wait_first(self) -> str:
        """Block until the question is stored ("turn") or the turn ended before ("done")."""
        with self.condition:
            self.condition.wait_for(lambda: self.accepted or self.finished)
            return "turn" if self.accepted else "done"

    def done_event(self) -> dict[str, Any] | None:
        with self.condition:
            for event in reversed(self.events):
                if event.get("type") == "done":
                    return event
        return None

    def frames(self, after: int = 0):
        """SSE frames of the events after ``after``, ending with the done frame."""
        position = max(0, int(after or 0))
        try:
            while True:
                with self.condition:
                    self.condition.wait_for(
                        lambda: len(self.events) > position or self.finished,
                        timeout=KEEPALIVE_SECONDS,
                    )
                    pending = self.events[position:]
                    finished = self.finished
                if not pending:
                    if finished:
                        return
                    yield ": keep-alive\n\n"
                    continue
                for index, event in _merged(pending, position):
                    data = json.dumps(event, ensure_ascii=False, default=str)
                    yield f"id: {index}\ndata: {data}\n\n"
                    if event.get("type") == "done":
                        return
                position += len(pending)
        except GeneratorExit:
            return


# Live turns of this process by conversation and request id.
_registry: dict[str, LiveTurn] = {}
_registry_lock = threading.RLock()
# Receipts whose settle failed in this process: the turn has ended, but the receipt still
# names this process as the one running it (see _ended_unsettled).
_unsettled: set[str] = set()


def _key(conversation_id: Any, request_id: Any) -> str:
    return f"{conversation_id}/{request_id}"


def register(live: LiveTurn) -> None:
    with _registry_lock:
        _registry[_key(live.conversation_id, live.request_id)] = live


def find(conversation_id: Any, request_id: Any) -> LiveTurn | None:
    with _registry_lock:
        return _registry.get(_key(conversation_id, request_id))


class StopToken:
    """«Стоп» for a turn answered as JSON: no event log, only the stop and the provider response to cancel."""

    def __init__(self, conversation_id: Any, request_id: str, owner_id: Any):
        self.conversation_id = conversation_id
        self.request_id = request_id
        self.owner_id = owner_id
        self.finished = False
        self.response_id: str | None = None
        self.stop_requested = threading.Event()

    def request_stop(self) -> str | None:
        self.stop_requested.set()
        return self.response_id

    def provider_event(self, event: dict[str, Any]) -> None:
        response_id = event.get("response_id") if isinstance(event, dict) else None
        if isinstance(response_id, str) and response_id:
            self.response_id = response_id


_stoppable: dict[str, StopToken] = {}


def register_stop(token: StopToken) -> None:
    with _registry_lock:
        _stoppable[_key(token.conversation_id, token.request_id)] = token


def unregister_stop(token: StopToken) -> None:
    token.finished = True
    with _registry_lock:
        key = _key(token.conversation_id, token.request_id)
        if _stoppable.get(key) is token:
            del _stoppable[key]


def find_stop(conversation_id: Any, request_id: Any) -> StopToken | None:
    with _registry_lock:
        return _stoppable.get(_key(conversation_id, request_id))


def unregister(live: LiveTurn) -> None:
    with _registry_lock:
        key = _key(live.conversation_id, live.request_id)
        if _registry.get(key) is live:
            del _registry[key]


def sse_response(live: LiveTurn, after: int = 0) -> Response:
    return Response(
        stream_with_context(live.frames(after)),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


# Durable receipts: chat/turn_request/<conversation>/<request_id>.


def _receipt_key(conversation_id: str, request_id: str) -> str:
    return "chat/turn_request/" + conversation_id + "/" + request_id


def _message_key(message_id: int) -> str:
    return "chat/message/" + str(message_id)


def _request_message_key(conversation_id: str, role: str, request_id: str) -> str:
    # The index storage.records commits with each message that carries a request id.
    return "chat/message-request/" + conversation_id + "/" + role + "/" + request_id


def _message_id(value: Any) -> int | None:
    """A message id as the store keeps it, or None for any other value."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _stored_message_id(conversation_id: str, role: str, request_id: str) -> int | None:
    """The id of the message of ``role`` that this turn request stored, or None."""
    return _message_id(get_store().get(_request_message_key(conversation_id, role, request_id)))


def fingerprint(data: dict[str, Any]) -> str:
    """What makes a re-sent turn the same turn: its text, model, photos and retry target."""
    params = data.get("params") if isinstance(data.get("params"), dict) else {}
    attachments = data.get("attachments")
    return hashlib.sha256(
        json.dumps(
            [
                data.get("message"),
                data.get("model") or params.get("model"),
                # The photos themselves (their data URLs), so a different photo is a different turn.
                attachments if isinstance(attachments, list) else [],
                data.get("retry_message_id"),
            ],
            ensure_ascii=False,
            default=str,
        ).encode()
    ).hexdigest()


def _running_body(conversation_id: str, request_id: str, user_message_id: Any, invocation_id: Any) -> dict[str, Any]:
    return {
        "status": "running",
        "request_id": request_id,
        "conversation_id": conversation_id,
        "user_message_id": user_message_id,
        "invocation_id": invocation_id,
    }


def _question_id(conversation_id: str, request_id: str, record: dict[str, Any]) -> Any:
    """The id of the question a turn stored: the receipt's, else the one the store indexes."""
    question_id = record.get("user_message_id")
    if question_id is None:
        question_id = _stored_message_id(conversation_id, "user", request_id)
    return question_id


def _ended_unsettled(conversation_id: str, request_id: str) -> bool:
    """True when this process's turn for the request ended with its settle failed, and no
    live entry is left: the receipt still names this process, but nothing runs for it."""
    with _registry_lock:
        return (
            _receipt_key(conversation_id, request_id) in _unsettled
            and find(conversation_id, request_id) is None
        )


def _receipt_answer(conversation_id: str, request_id: str, record: dict[str, Any]) -> Any:
    """The answer for a turn known only by its receipt: stored, failed, running here, or lost.

    A reply is stored exactly when the index that add_message commits with it names one,
    whatever the receipt says: the receipt may not have been settled (the settle failed,
    or the process stopped after the reply was stored). Without a stored reply, a turn
    whose receipt is done has failed, a turn running in this process is running, and any
    other turn is lost.
    """
    reply_id = _stored_message_id(conversation_id, "assistant", request_id)
    if reply_id is not None:
        return jsonify(
            {
                "status": "stored",
                "request_id": request_id,
                "conversation_id": conversation_id,
                "user_message_id": _question_id(conversation_id, request_id, record),
                "invocation_id": record.get("invocation_id"),
                "message_id": reply_id,
                "http_status": record.get("http_status"),
            }
        ), 200
    if record.get("status") == "done":
        return jsonify(
            error_body(
                "turn_failed",
                status="failed",
                request_id=request_id,
                conversation_id=conversation_id,
                user_message_id=_question_id(conversation_id, request_id, record),
                invocation_id=record.get("invocation_id"),
            )
        ), 500
    if record.get("process") == PROCESS_TOKEN and not _ended_unsettled(
        conversation_id, request_id
    ):
        return jsonify(
            _running_body(
                conversation_id,
                request_id,
                record.get("user_message_id"),
                record.get("invocation_id"),
            )
        ), 202
    question_id = _question_id(conversation_id, request_id, record)
    return jsonify(
        error_body(
            "turn_lost",
            # Not stored: sending the question again runs the turn (see _claim).
            detail_of=None if question_id is not None else "turn_lost_unsaved",
            status="lost",
            request_id=request_id,
            conversation_id=conversation_id,
            user_message_id=question_id,
            invocation_id=record.get("invocation_id"),
        )
    ), 409


def _reconciled(
    tx: Any, conversation_id: str, request_id: str, record: dict[str, Any]
) -> dict[str, Any]:
    """The receipt of a turn that ended in this process while its settle failed.

    A stored reply makes it done; otherwise the turn is lost. The receipt is no longer
    held by this process, so it is not read as running. Written in the caller's
    transaction; a receipt this process does not hold is returned unchanged.
    """
    if record.get("process") != PROCESS_TOKEN:
        return record
    reply_id = _message_id(
        tx.get(_request_message_key(conversation_id, "assistant", request_id))
    )
    question_id = record.get("user_message_id")
    if question_id is None:
        question_id = _message_id(
            tx.get(_request_message_key(conversation_id, "user", request_id))
        )
    reconciled = {
        **record,
        "status": "done" if reply_id is not None else "lost",
        "updated_at": time.time(),
        "process": None,
        "user_message_id": question_id,
    }
    tx.set(_receipt_key(conversation_id, request_id), reconciled)
    return reconciled


def _reconcile_ended(conversation_id: str, request_id: str) -> dict[str, Any] | None:
    """Reconcile the receipt of a turn whose settle failed here; None when it is gone.

    The unsettled condition is checked again under the lock that admit holds while it claims
    a receipt: a receipt a new POST has claimed since the caller looked is returned as it is.
    """
    with _registry_lock, get_store().transaction() as tx:
        record = tx.get(_receipt_key(conversation_id, request_id))
        if record is None:
            return None
        if not _ended_unsettled(conversation_id, request_id):
            return record
        return _reconciled(tx, conversation_id, request_id, record)


def receipt_response(conversation_id: str, request_id: str) -> Any:
    """GET /api/chat/turns/<request_id>/events for a turn that is not live here."""
    record = get_store().get(_receipt_key(conversation_id, request_id))
    if (
        record is not None
        and record.get("process") == PROCESS_TOKEN
        and _ended_unsettled(conversation_id, request_id)
    ):
        record = _reconcile_ended(conversation_id, request_id)
    if record is None:
        return jsonify(error_body("turn_request_not_found", request_id=request_id)), 404
    return _receipt_answer(conversation_id, request_id, record)


class Admission:
    """A turn request with a request_id: its receipt, and the answer when it must not run."""

    def __init__(
        self,
        conversation_id: Any,
        request_id: Any,
        fingerprint: str | None,
        response: Any = None,
        live: LiveTurn | None = None,
    ):
        self.conversation_id = conversation_id
        self.request_id = request_id
        self.fingerprint = fingerprint
        self.response = response
        self.live = live
        self.user_message_id: Any = None

    @property
    def _key(self) -> str:
        return _receipt_key(self.conversation_id, self.request_id)

    def mark_running(self, user_message_id: Any, invocation: Any) -> None:
        """The question is stored: a re-sent request now waits for this turn instead of running."""
        self.user_message_id = user_message_id
        try:
            with get_store().transaction() as tx:
                record = tx.get(self._key)
                if record is None:
                    # The conversation was deleted meanwhile; nothing is recreated.
                    return
                tx.set(
                    self._key,
                    {
                        **record,
                        "status": "running",
                        "updated_at": time.time(),
                        "process": PROCESS_TOKEN,
                        "user_message_id": user_message_id,
                        "invocation_id": getattr(invocation, "invocation_id", None),
                        "trace_id": getattr(invocation, "trace_id", None),
                    },
                )
                # A retried question was stored before this request: from this commit on it
                # carries the request id too, so a re-send finds it (the question of a new
                # request already carries it from storage.records).
                question = tx.get(_message_key(user_message_id))
                if (
                    isinstance(question, dict)
                    and question.get("conversation_id") == self.conversation_id
                    and question.get("request_id") is None
                ):
                    tx.set(
                        _message_key(user_message_id),
                        {**question, "request_id": self.request_id},
                    )
                tx.set(
                    _request_message_key(self.conversation_id, "user", self.request_id),
                    user_message_id,
                )
        except Exception:
            logger.exception("Turn receipt %s was not marked running", self.request_id)
            # Fail closed: without the receipt the provider is not asked, and the turn's
            # error path answers the request.
            raise

    def settle(self, http_status: int, body: Any) -> None:
        """Record how the turn ended; a turn that stored nothing may run again.

        Called from a finally, so every way a turn ends reaches it. When this fails the
        receipt keeps what it had: a reply stored for the request is still found by
        reads (_receipt_answer), so the failure never turns a stored answer into a lost one.
        """
        body = body if isinstance(body, dict) else {}
        stored_id = (
            self.user_message_id
            if self.user_message_id is not None
            else body.get("user_message_id")
        )
        try:
            if stored_id is None:
                # A question stored before mark_running, when the turn ended first.
                stored_id = _stored_message_id(
                    self.conversation_id, "user", self.request_id
                )
            with get_store().transaction() as tx:
                record = tx.get(self._key)
                if record is None:
                    return
                if stored_id is None:
                    tx.delete(self._key)
                    return
                tx.set(
                    self._key,
                    {
                        **record,
                        "status": "done",
                        "updated_at": time.time(),
                        "user_message_id": record.get("user_message_id")
                        if record.get("user_message_id") is not None
                        else stored_id,
                        "invocation_id": record.get("invocation_id")
                        or body.get("invocation_id"),
                        "trace_id": record.get("trace_id") or body.get("trace_id"),
                        "http_status": int(http_status),
                    },
                )
        except Exception:
            logger.exception("Turn receipt %s was not settled", self.request_id)
            # The turn has ended here, so its receipt must not be read as running.
            with _registry_lock:
                _unsettled.add(self._key)


def _receipt(print_: str, now: float, created_at: float, process: Any) -> dict[str, Any]:
    """A receipt that has not stored its question yet."""
    return {
        "status": "started",
        "created_at": created_at,
        "updated_at": now,
        "fingerprint": print_,
        "process": process,
        "user_message_id": None,
        "invocation_id": None,
        "trace_id": None,
        "http_status": None,
    }


def _claim(conversation_id: str, request_id: str, print_: str) -> tuple[str, dict[str, Any] | None]:
    """Claim the receipt for this process, or say why the turn must not run."""
    key = _receipt_key(conversation_id, request_id)
    with get_store().transaction() as tx:
        record = tx.get(key)
        if record is not None:
            if record.get("fingerprint") != print_:
                return "conflict", record
            if record.get("status") == "done":
                return "stored", record
            if record.get("process") == PROCESS_TOKEN:
                if not _ended_unsettled(conversation_id, request_id):
                    return "running", record
                # This process ended the turn and its settle failed: reconcile the receipt.
                return "lost", _reconciled(tx, conversation_id, request_id, record)
            if record.get("user_message_id") is not None:
                return "lost", record
        now = time.time()
        created_at = record.get("created_at", now) if record else now
        # The question may be stored although the receipt does not say so: a process
        # stopped between the store and its receipt. Such a question ran once already,
        # so the turn is lost, not run again.
        stored_id = tx.get(_request_message_key(conversation_id, "user", request_id))
        if stored_id is not None:
            lost = {
                **_receipt(print_, now, created_at, None),
                "user_message_id": stored_id,
            }
            tx.set(key, lost)
            return "lost", lost
        # A new request, or one a dead process claimed before it stored the question.
        tx.set(key, _receipt(print_, now, created_at, PROCESS_TOKEN))
    # The receipt runs in this process now, so it is no longer one whose settle failed.
    with _registry_lock:
        _unsettled.discard(key)
    return "claimed", None


def _store_unavailable(conversation_id: Any, request_id: str) -> Admission:
    """The refusal of a request the store could not admit: nothing was stored for it."""
    return Admission(
        conversation_id,
        request_id,
        None,
        response=(
            jsonify(error_body("turn_store_unavailable", request_id=request_id)),
            503,
        ),
    )


def admit(data: Any, wants_live: bool) -> Admission | None:
    """Decide whether a POST /api/chat with a request_id runs, waits or is answered at once.

    None means the request has no request_id and runs as before. An Admission with
    ``response`` is answered with that response. When the store cannot admit the request,
    it is refused (503) before anything is stored, so the browser may send it again.
    """
    if not isinstance(data, dict) or data.get("request_id") is None:
        return None
    request_id = data.get("request_id")
    conversation_id = data.get("conversation_id")
    if not isinstance(request_id, str) or not REQUEST_ID_PATTERN.fullmatch(request_id):
        return Admission(
            conversation_id,
            request_id,
            None,
            response=(
                jsonify(error_body("invalid_params", message=REQUEST_ID_MESSAGE)),
                400,
            ),
        )
    if not isinstance(conversation_id, str) or not conversation_id.strip():
        return Admission(
            conversation_id,
            request_id,
            None,
            response=(jsonify(error_body("conversation_id is required")), 400),
        )
    try:
        owner_id = get_current_owner_id(required=False)
        allowed = check_access(conversation_id, owner_id)
    except Exception:
        logger.exception("Turn request %s was refused: access could not be checked", request_id)
        return _store_unavailable(conversation_id, request_id)
    if not allowed:
        return Admission(
            conversation_id,
            request_id,
            None,
            response=(jsonify(error_body("conversation_not_found")), 404),
        )
    print_ = fingerprint(data)
    with _registry_lock:
        live = find(conversation_id, request_id)
        if live is not None and live.owner_id != owner_id:
            # Another caller's turn is answered from its receipt, as GET /api/chat/turns does.
            return Admission(
                conversation_id,
                request_id,
                print_,
                response=receipt_response(conversation_id, request_id),
            )
        if live is not None:
            if live.fingerprint != print_:
                return Admission(
                    conversation_id,
                    request_id,
                    print_,
                    response=(
                        jsonify(
                            error_body("turn_request_conflict", request_id=request_id)
                        ),
                        409,
                    ),
                )
            if wants_live:
                return Admission(
                    conversation_id,
                    request_id,
                    print_,
                    response=sse_response(live, 0),
                    live=live,
                )
            return Admission(
                conversation_id,
                request_id,
                print_,
                response=(
                    jsonify(
                        _running_body(
                            conversation_id,
                            request_id,
                            live.user_message_id,
                            live.invocation_id,
                        )
                    ),
                    202,
                ),
            )
        try:
            decision, record = _claim(conversation_id, request_id, print_)
        except Exception:
            logger.exception("Turn request %s was refused: its receipt was not claimed", request_id)
            return _store_unavailable(conversation_id, request_id)
        if decision == "claimed":
            admission = Admission(conversation_id, request_id, print_)
            if wants_live:
                live = LiveTurn(conversation_id, request_id, owner_id)
                live.fingerprint = print_
                register(live)
                admission.live = live
            return admission
        if decision == "conflict":
            response = (
                jsonify(error_body("turn_request_conflict", request_id=request_id)),
                409,
            )
        else:
            response = _receipt_answer(conversation_id, request_id, record or {})
        return Admission(conversation_id, request_id, print_, response=response)


__all__ = [
    "KEEPALIVE_SECONDS",
    "PROCESS_TOKEN",
    "REQUEST_ID_MESSAGE",
    "REQUEST_ID_PATTERN",
    "Admission",
    "LiveTurn",
    "admit",
    "find",
    "fingerprint",
    "receipt_response",
    "register",
    "sse_response",
    "unregister",
]
