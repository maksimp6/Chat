"""Stream mixin of the Responses client: the POST attempt loop shared by streamed and
polled requests, live reading of a streamed reply, resuming a broken stream by id and
the normalized events a live chat turn shows.

A POST is sent again only before a response id is known, and only after a connection
that was never made or an answer of 408/425/429/503 or a rate-limit error event. A
streaming request the provider refuses fails with its error; the stream mode is never
changed. An attempt the provider may have accepted is not sent again, and once a response
id is known no POST is sent at all: a broken stream is resumed by id, or polled after a
transient failure. A resume the provider refuses is an error that names the response.
"""

import http.client
import logging
import math
import time

import requests
import urllib3

from yandex_client_modules.api_logger import api_logger
from yandex_client_modules.errors import YandexClientError
from yandex_client_modules.host import bound
from yandex_client_modules.provider_retry import (
    OUTCOME_UNKNOWN,
    RATE_LIMIT_EVENT_CODE,
    backoff_delay,
    classify_post,
    exhausted_text,
    failure_reason,
    known_not_accepted,
    retry_after_seconds,
    retry_after_too_long_text,
    server_error_unknown_text,
    silent_stream_text,
)
from yandex_client_modules.request_utils import sanitize_for_log
from yandex_client_modules.sse import COMMENT_MARKER, iter_sse_events

logger = logging.getLogger("logos.yandex_client_modules.stream_mixin")

# Errors that end the reading of a stream; each one is an interruption, not a failure.
STREAM_ERRORS = (
    requests.RequestException,
    urllib3.exceptions.HTTPError,
    http.client.HTTPException,
    OSError,
)

STREAM_STARTED_EVENTS = ("response.created", "response.queued", "response.in_progress")
TERMINAL_EVENTS = ("response.completed", "response.incomplete", "response.failed")
# The stream resume request is not supported when it is refused with one of these; a
# refused resume is an error that names the response, never a poll.
RESUME_UNSUPPORTED_STATUSES = (400, 405, 406, 415, 422, 501)

# Hosted tool events and what the status line says about them: (tool kind, phase).
_TOOL_EVENTS = {
    "response.web_search_call.in_progress": ("web_search", "start"),
    "response.web_search_call.searching": ("web_search", "start"),
    "response.web_search_call.completed": ("web_search", "done"),
    "response.file_search_call.in_progress": ("file_search", "start"),
    "response.file_search_call.searching": ("file_search", "start"),
    "response.file_search_call.completed": ("file_search", "done"),
    "response.code_interpreter_call.in_progress": ("code_interpreter", "start"),
    "response.code_interpreter_call.interpreting": ("code_interpreter", "start"),
    "response.code_interpreter_call.completed": ("code_interpreter", "done"),
    "response.mcp_call.in_progress": ("mcp", "start"),
    "response.mcp_call.completed": ("mcp", "done"),
    "response.mcp_call.failed": ("mcp", "failed"),
    "response.mcp_list_tools.in_progress": ("mcp_list_tools", "start"),
    "response.mcp_list_tools.completed": ("mcp_list_tools", "done"),
    "response.mcp_list_tools.failed": ("mcp_list_tools", "failed"),
    "response.image_generation_call.in_progress": ("image_generation", "start"),
    "response.image_generation_call.generating": ("image_generation", "start"),
    "response.image_generation_call.completed": ("image_generation", "done"),
}

def emit_event(on_event, event):
    """Deliver one normalized event; a failing receiver never breaks the request."""
    if on_event is None:
        return
    try:
        on_event(event)
    except Exception:
        logger.exception("Live event %s was not delivered", event.get("kind"))


def _finite(value):
    if value is None or not math.isfinite(value):
        return None
    return round(value, 3)


def note_retry(
    trace,
    on_event,
    *,
    operation,
    attempt,
    reason,
    status_code,
    error_type,
    delay,
    retry_after,
    remaining,
    response_id=None,
    step=None,
):
    """Record a scheduled retry in the trace and tell the live turn about it."""
    if isinstance(trace, bound.ExecutionTrace):
        trace.add_event(
            "provider_retry_scheduled",
            {
                "operation": operation,
                "attempt": attempt,
                "reason": reason,
                "status_code": status_code,
                "error_type": error_type,
                "delay_s": _finite(delay),
                "retry_after_s": _finite(retry_after),
                "remaining_s": _finite(remaining),
                "response_id": response_id,
                "step": step,
                "correlation_id": trace.get_step_correlation_id(step)
                if step is not None
                else None,
            },
        )
    emit_event(
        on_event,
        {
            "kind": "retry",
            "operation": operation,
            "attempt": attempt,
            "reason": reason,
            "status_code": status_code,
            "delay_s": _finite(delay),
            "retry_after_s": _finite(retry_after),
        },
    )


def note_retry_exhausted(
    trace, *, operation, attempts, reason, status_code, elapsed, step=None
):
    """Record that a retry could not happen before the deadline."""
    if isinstance(trace, bound.ExecutionTrace):
        trace.add_event(
            "provider_retry_exhausted",
            {
                "operation": operation,
                "attempts": attempts,
                "reason": reason,
                "status_code": status_code,
                "elapsed_s": _finite(elapsed),
                "step": step,
                "correlation_id": trace.get_step_correlation_id(step)
                if step is not None
                else None,
            },
        )


def is_event_stream(resp):
    headers = getattr(resp, "headers", None) or {}
    try:
        content_type = headers.get("Content-Type") or ""
    except Exception:
        content_type = ""
    return "text/event-stream" in str(content_type).lower()


def _close(resp):
    if resp is None:
        return
    try:
        resp.close()
    except Exception:
        logger.debug("Provider response was not closed", exc_info=True)


def _response_detail(resp):
    """The sanitized error body of a JSON or text answer, as api_request_error keeps it."""
    if resp is None or is_event_stream(resp):
        return None
    try:
        return sanitize_for_log(resp.json())
    except ValueError:
        pass
    except Exception:
        return None
    try:
        return resp.text[:4000]
    except Exception:
        return None


def _http_error(resp):
    """The HTTPError requests raises for this answer, so its text reads as before."""
    try:
        resp.raise_for_status()
    except requests.HTTPError as exc:
        return exc
    return None


def _event_error(event):
    """The code and message of an SSE error event, flat or in a nested error object."""
    nested = event.get("error") if isinstance(event.get("error"), dict) else {}
    code = event.get("code") or nested.get("code")
    message = event.get("message") or nested.get("message")
    return (str(code) if code else None), (str(message) if message else None)


def _read_head(resp, deadline=None):
    """Read a streamed reply until it names its response, ends, or reports an error.

    Returns the open event iterator, the events read so far and how the head ended:
    "started", "error", "eof", or "idle" when the monotonic ``deadline`` passed first.
    The caller starts that window when the stream opened, not with its POST retries,
    so a request accepted late in the retry budget still gets the whole wait.
    Keep-alive comments are no progress: they never extend the wait, so a provider
    that only keeps the connection open cannot hold the request forever.
    """
    events = iter_sse_events(resp, comments=True)
    head = []
    for event in events:
        event_type = event.get("type")
        if event_type != COMMENT_MARKER:
            head.append(event)
            if event_type == "error":
                return events, head, "error"
            if event_type == "[DONE]":
                return events, head, "eof"
            response = event.get("response")
            if event_type in TERMINAL_EVENTS or (
                isinstance(response, dict) and response.get("id")
            ):
                return events, head, "started"
        if deadline is not None and time.monotonic() >= deadline:
            return events, head, "idle"
    return events, head, "eof"


class LiveStream:
    """An open streamed reply: its connection, its event iterator and what was read."""

    def __init__(self, resp, events, pending):
        self.resp = resp
        self.events = events
        self.pending = list(pending)
        self.response_id = None
        self.last_seq = None
        # output_index -> the item from response.output_item.done
        self.items = {}
        self.text = []
        self.events_received = 0
        self.invalid_events = 0
        self.started = False
        self.status = None
        self.tools_started = set()
        # item_id -> (name, server_label) of MCP items, which their progress events lack
        self.mcp_items = {}
        self.deadline = None

    def next_event(self):
        """The next event, or None at the end of the stream."""
        if self.pending:
            return self.pending.pop(0)
        return next(self.events, None)


class YandexStreamMixin:
    def _post_response(
        self,
        url,
        payload,
        headers,
        *,
        request_timeout,
        poll_timeout,
        trace,
        step,
        on_event=None,
        request_start_timestamp,
        request_start_perf,
    ):
        """Send the POST of one logical request until the provider has taken it.

        Returns the requests response of a JSON answer, or a LiveStream whose head
        named the response. One trace step, one api_requests entry and one quota
        reservation cover every attempt; ask() made them before this loop.
        """
        traced = isinstance(trace, bound.ExecutionTrace)
        correlation_id = (
            trace.get_step_correlation_id(step) if traced and step is not None else None
        )
        first = time.monotonic()
        deadline = first + poll_timeout
        stream_requested = payload.get("stream") is True
        sent = 0
        retries = 0
        while True:
            sent += 1
            attempt_headers = dict(headers)
            if stream_requested:
                attempt_headers["Accept"] = "text/event-stream"
                attempt_headers["Accept-Encoding"] = "identity"
            if sent > 1:
                self._log_request("POST", url, json=payload)
            resp = None
            exc = None
            try:
                resp = self.session.post(
                    url,
                    json=payload,
                    headers=attempt_headers,
                    timeout=request_timeout,
                    stream=stream_requested,
                )
                if stream_requested and not is_event_stream(resp):
                    # A JSON answer to a streaming request is read whole here, like
                    # the answer of any other POST.
                    resp.content
            except requests.RequestException as error:
                exc = error
                _close(resp)
                resp = None
            event_stream = resp is not None and is_event_stream(resp)
            if resp is not None:
                if event_stream:
                    api_logger.info("[RESP] %s %s (stream)", resp.status_code, resp.url)
                else:
                    self._log_response(resp)
            outcome = classify_post(exc, resp, stream_requested)
            error_event = None
            error_code = None
            silent = None
            if outcome == "ok" and event_stream:
                # The head gets its own window from when the stream opened: the retry
                # budget may be almost spent by an attempt the provider just accepted.
                head_deadline = time.monotonic() + poll_timeout
                head_started = time.monotonic()
                try:
                    events, head, head_state = _read_head(resp, head_deadline)
                except STREAM_ERRORS as error:
                    exc = error
                    outcome = "ambiguous"
                else:
                    if head_state == "started":
                        return LiveStream(resp, events, head)
                    if head_state == "error":
                        error_event = head[-1]
                        error_code, _ = _event_error(error_event)
                        # Only a rate limit is sent again; any other or missing code may
                        # have been accepted, so it is not.
                        outcome = (
                            "transient"
                            if error_code == RATE_LIMIT_EVENT_CODE
                            else "ambiguous"
                        )
                    else:
                        # EOF, or only keep-alives until the deadline: the provider may
                        # have taken the request, so it is never sent again.
                        outcome = "ambiguous"
                        silent = (head_state, time.monotonic() - head_started)
            elif outcome == "ok":
                return resp

            failure = {
                "url": url,
                "payload": payload,
                "trace": trace,
                "step": step,
                "correlation_id": correlation_id,
                "exc": exc,
                "resp": resp,
                "error_event": error_event,
                "attempts": sent,
                "request_start_timestamp": request_start_timestamp,
                "request_start_perf": request_start_perf,
            }
            status_code = getattr(resp, "status_code", None) if resp is not None else None
            if outcome in ("not_sent", "transient"):
                retries += 1
                reason = (
                    "provider_error_event"
                    if error_event is not None
                    else failure_reason(exc, resp)
                )
                retry_after = (
                    retry_after_seconds(getattr(resp, "headers", None))
                    if resp is not None and error_event is None
                    else None
                )
                now = time.monotonic()
                remaining = deadline - now
                delay = backoff_delay(retries, now - first, remaining, retry_after)
                if delay is not None:
                    _close(resp)
                    note_retry(
                        trace,
                        on_event,
                        operation="post",
                        attempt=retries,
                        reason=reason,
                        status_code=status_code,
                        error_type=type(exc).__name__ if exc is not None else None,
                        delay=delay,
                        retry_after=retry_after,
                        remaining=remaining,
                        step=step,
                    )
                    api_logger.info(
                        "[RETRY] POST %s in %.1f s (%s, attempt %s)",
                        url,
                        delay,
                        reason,
                        retries,
                    )
                    time.sleep(delay)
                    continue
                note_retry_exhausted(
                    trace,
                    operation="post",
                    attempts=sent,
                    reason=reason,
                    status_code=status_code,
                    elapsed=now - first,
                    step=step,
                )
                user_text = (
                    retry_after_too_long_text(retry_after, poll_timeout)
                    if retry_after is not None and retry_after >= remaining
                    else exhausted_text(now - first, reason)
                )
                self._raise_post_failure(
                    **failure,
                    outcome="exhausted",
                    user_text=user_text,
                    request_not_accepted=known_not_accepted(exc, resp, error_code),
                )
            if outcome == "ambiguous":
                if traced:
                    trace.add_event(
                        "provider_post_outcome_unknown",
                        {
                            "step": step,
                            "correlation_id": correlation_id,
                            "attempts": sent,
                            "error_type": type(exc).__name__
                            if exc is not None
                            else None,
                            "status_code": status_code,
                            "head": silent[0] if silent is not None else None,
                            "head_elapsed": round(silent[1], 1)
                            if silent is not None
                            else None,
                        },
                    )
                # An HTTP error status is an answer, not a broken connection: say so.
                self._raise_post_failure(
                    **failure,
                    outcome="ambiguous",
                    user_text=silent_stream_text(silent, poll_timeout)
                    if silent is not None
                    else server_error_unknown_text(status_code)
                    if exc is None
                    and isinstance(status_code, int)
                    and status_code >= 500
                    else OUTCOME_UNKNOWN,
                    request_not_accepted=False,
                )
            self._raise_post_failure(
                **failure,
                outcome="fatal",
                user_text=None,
                request_not_accepted=False,
            )

    def _raise_post_failure(
        self,
        *,
        url,
        payload,
        trace,
        step,
        correlation_id,
        exc,
        resp,
        error_event,
        attempts,
        outcome,
        user_text,
        request_not_accepted,
        request_start_timestamp,
        request_start_perf,
    ):
        """End the request as today's POST failure, with its attempts and outcome.

        ``request_not_accepted`` alone decides the quota release: the caller computes
        it, and True means the provider is known not to have accepted the POST.
        """
        error_timestamp = time.time()
        status_code = getattr(resp, "status_code", None) if resp is not None else None
        if not isinstance(status_code, int) or status_code < 400:
            status_code = None
        cause = exc
        if error_event is not None:
            code, message = _event_error(error_event)
            detail = sanitize_for_log(error_event)
            error_message = (
                "Provider stream error"
                + (f" {code}" if code else "")
                + (f": {message}" if message else "")
            )
        else:
            detail = _response_detail(resp)
            if exc is not None:
                error_message = str(exc)
            elif status_code is not None:
                cause = _http_error(resp)
                error_message = (
                    str(cause) if cause is not None else f"status {status_code}"
                )
            else:
                error_message = "Provider stream ended before it named the response"
            if status_code is not None:
                error_message = f"HTTP {status_code}: {error_message}"
        _close(resp)
        if isinstance(trace, bound.ExecutionTrace):
            trace.add_event(
                "api_request_error",
                {
                    "method": "POST",
                    "url": url,
                    "step": step,
                    "correlation_id": correlation_id,
                    "model": payload.get("model"),
                    "status_code": status_code,
                    "error": error_message,
                    "detail": detail,
                    "start_timestamp": request_start_timestamp,
                    "end_timestamp": error_timestamp,
                    "timing_ms": round(
                        (time.perf_counter() - request_start_perf) * 1000, 2
                    ),
                    "attempts": attempts,
                    "outcome": outcome,
                },
            )
        error = YandexClientError(
            error_message, status_code=status_code, user_text=user_text
        )
        error.request_not_accepted = bool(request_not_accepted)
        raise error from cause

    def _finish_stream(
        self,
        live,
        *,
        url,
        headers,
        request_timeout,
        poll_timeout,
        trace,
        step,
        on_event=None,
        on_provider_response=None,
        request_start_timestamp,
        request_start_perf,
        provider_key_id=None,
    ):
        """Read a streamed reply to its terminal state; resume or poll it when it breaks."""
        live.url = url
        live.headers = headers
        live.request_timeout = request_timeout
        live.poll_timeout = poll_timeout
        live.trace = trace
        live.traced = isinstance(trace, bound.ExecutionTrace)
        live.step = step
        live.correlation_id = (
            trace.get_step_correlation_id(step)
            if live.traced and step is not None
            else None
        )
        live.on_event = on_event
        live.on_provider_response = on_provider_response
        live.request_start_timestamp = request_start_timestamp
        live.request_start_perf = request_start_perf
        live.provider_key_id = provider_key_id
        live.deadline = time.monotonic() + poll_timeout
        resumed_connection = False
        try:
            while True:
                sequence_before = live.last_seq
                outcome, value = self._read_stream(live)
                if outcome == "terminal":
                    return self._stream_terminal(live, value)
                if live.traced:
                    trace.add_event(
                        "provider_stream_interrupted",
                        {
                            "step": step,
                            "correlation_id": live.correlation_id,
                            "response_id": live.response_id,
                            "last_sequence_number": live.last_seq,
                            "events_received": live.events_received,
                            "error_type": value,
                        },
                    )
                emit_event(
                    on_event,
                    {
                        "kind": "stream_interrupted",
                        "response_id": live.response_id,
                        "last_sequence_number": live.last_seq,
                    },
                )
                _close(live.resp)
                if not live.response_id:
                    raise YandexClientError(
                        "Provider stream ended before it named the response",
                        user_text=OUTCOME_UNKNOWN,
                    )
                if resumed_connection and live.last_seq == sequence_before:
                    # A resumed connection that ended without one new event shows no
                    # progress; the stored response is read instead.
                    return self._poll_fallback(live)
                resumed = self._resume_stream(live)
                if isinstance(resumed, dict):
                    return resumed
                if resumed is None:
                    return self._poll_fallback(live)
                resumed_connection = True
                live.resp = resumed
                live.events = iter_sse_events(resumed, comments=True)
                live.pending = []
        except YandexClientError:
            self._keep_stream_partial(live)
            raise
        finally:
            _close(live.resp)

    def _read_stream(self, live):
        """Read events until the reply ends: ("terminal", response) or ("interrupted", cause)."""
        try:
            while True:
                event = live.next_event()
                if event is None:
                    return "interrupted", "eof"
                event_type = event.get("type")
                if event_type == COMMENT_MARKER:
                    # A keep-alive is no progress: it is not counted and does not move
                    # the deadline, so a reply that only keeps the connection open ends
                    # here and is resumed or polled within the poll budget.
                    if time.monotonic() >= live.deadline:
                        return "interrupted", "stream_idle"
                    continue
                live.events_received += 1
                live.deadline = time.monotonic() + live.poll_timeout
                if event_type == "__invalid__":
                    live.invalid_events += 1
                    continue
                if event_type == "[DONE]":
                    return "interrupted", "done_without_terminal"
                sequence = event.get("sequence_number")
                if isinstance(sequence, int) and not isinstance(sequence, bool):
                    if live.last_seq is not None and sequence <= live.last_seq:
                        continue
                    live.last_seq = sequence
                response = event.get("response")
                if (
                    isinstance(response, dict)
                    and response.get("id")
                    and not live.started
                ):
                    self._stream_started(live, response)
                if event_type == "error":
                    return "interrupted", "provider_error_event"
                if event_type in TERMINAL_EVENTS:
                    return "terminal", response if isinstance(response, dict) else {}
                self._stream_progress(live, event)
        except STREAM_ERRORS as exc:
            return "interrupted", type(exc).__name__

    def _stream_started(self, live, snapshot):
        """The response is named: the POST is complete and billing starts from here."""
        live.started = True
        live.response_id = snapshot.get("id")
        live.status = snapshot.get("status")
        now = time.time()
        timing_ms = round((time.perf_counter() - live.request_start_perf) * 1000, 2)
        if live.traced:
            live.trace.add_response(
                snapshot,
                step_index=live.step or 1,
                start_timestamp=live.request_start_timestamp,
                end_timestamp=now,
                timing_ms=timing_ms,
                kind="initial_response",
                provider_key_id=live.provider_key_id,
            )
            live.trace.add_event(
                "api_request_completed",
                {
                    "method": "POST",
                    "url": live.url,
                    "step": live.step,
                    "correlation_id": live.correlation_id,
                    "start_timestamp": live.request_start_timestamp,
                    "end_timestamp": now,
                    "timing_ms": timing_ms,
                    "response_id": live.response_id,
                    "status": live.status,
                    "stream": True,
                },
            )
            live.trace.add_event(
                "provider_stream_started",
                {
                    "step": live.step,
                    "correlation_id": live.correlation_id,
                    "response_id": live.response_id,
                    "status": live.status,
                    "first_event_ms": timing_ms,
                },
            )
        if live.on_provider_response is not None:
            live.on_provider_response(snapshot)
        emit_event(
            live.on_event,
            {
                "kind": "response_status",
                "status": live.status,
                "response_id": live.response_id,
            },
        )

    def _stream_progress(self, live, event):
        """Turn one provider event into the normalized event of a live turn; deltas are never traced."""
        event_type = event.get("type") or ""
        on_event = live.on_event
        if event_type in STREAM_STARTED_EVENTS:
            response = event.get("response")
            status = response.get("status") if isinstance(response, dict) else None
            if status and status != live.status:
                live.status = status
                emit_event(
                    on_event,
                    {
                        "kind": "response_status",
                        "status": status,
                        "response_id": live.response_id,
                    },
                )
            return
        if event_type == "response.output_text.delta":
            text = event.get("delta")
            if isinstance(text, str) and text:
                live.text.append(text)
                emit_event(on_event, {"kind": "text_delta", "text": text})
            return
        if event_type in (
            "response.reasoning_text.delta",
            "response.reasoning_summary_text.delta",
        ):
            text = event.get("delta")
            if not isinstance(text, str) or not text:
                return
            summary = event_type == "response.reasoning_summary_text.delta"
            item_id = event.get("item_id")
            part = (
                f"{item_id}:s{event.get('summary_index')}"
                if summary
                else f"{item_id}:{event.get('content_index')}"
            )
            emit_event(
                on_event,
                {
                    "kind": "reasoning_delta",
                    "text": text,
                    "summary": summary,
                    "part": part,
                },
            )
            return
        if event_type == "response.output_item.added":
            item = event.get("item")
            if not isinstance(item, dict):
                return
            if item.get("type") == "function_call":
                emit_event(
                    on_event,
                    {
                        "kind": "tool_progress",
                        "tool": "function",
                        "phase": "requested",
                        "name": item.get("name"),
                        "server_label": None,
                    },
                )
            elif item.get("type") in ("mcp_call", "mcp_list_tools"):
                live.mcp_items[item.get("id")] = (
                    item.get("name"),
                    item.get("server_label"),
                )
            return
        if event_type == "response.output_item.done":
            index = event.get("output_index")
            item = event.get("item")
            if (
                isinstance(index, int)
                and not isinstance(index, bool)
                and isinstance(item, dict)
            ):
                live.items[index] = item
            return
        tool = _TOOL_EVENTS.get(event_type)
        if tool is None:
            return
        kind, phase = tool
        item_id = event.get("item_id")
        if phase == "start":
            if (kind, item_id) in live.tools_started:
                return
            live.tools_started.add((kind, item_id))
        name, server_label = (
            live.mcp_items.get(item_id, (None, None))
            if kind in ("mcp", "mcp_list_tools")
            else (None, None)
        )
        emit_event(
            on_event,
            {
                "kind": "tool_progress",
                "tool": kind,
                "phase": phase,
                "name": name,
                "server_label": server_label,
            },
        )

    def _stream_terminal(self, live, final):
        """The terminal response, completed from the streamed items and the stored usage."""
        final = dict(final) if isinstance(final, dict) else {}
        output = final.get("output")
        if (not isinstance(output, list) or not output) and live.items:
            final["output"] = [live.items[index] for index in sorted(live.items)]
        if not final.get("id") and live.response_id:
            final["id"] = live.response_id
        if final.get("usage") is None and final.get("id"):
            # Usage is read from the stored response, so the turn is billed as before.
            final = self._wait(
                final["id"],
                timeout=live.poll_timeout,
                execution_trace=live.trace,
                trace_step=live.step,
                headers=live.headers,
                on_event=live.on_event,
            )
        timing_ms = round((time.perf_counter() - live.request_start_perf) * 1000, 2)
        if live.traced and isinstance(final, dict):
            live.trace.add_response(
                final,
                step_index=live.step or 1,
                start_timestamp=live.request_start_timestamp,
                end_timestamp=time.time(),
                timing_ms=timing_ms,
                kind="stream_response",
                provider_key_id=live.provider_key_id,
            )
            live.trace.add_event(
                "provider_stream_completed",
                {
                    "step": live.step,
                    "correlation_id": live.correlation_id,
                    "response_id": final.get("id") or live.response_id,
                    "status": final.get("status"),
                    "events_received": live.events_received,
                    "timing_ms": timing_ms,
                },
            )
        return final

    def _resume_stream(self, live):
        """Reconnect to a broken stream by id.

        Returns an open SSE response, a terminal response read as JSON, or None for the
        poll fallback of a transient failure: a stream without sequence numbers cannot be
        resumed at the right place, a 404 may be a stored response not yet visible, and
        retries ran out. A resume the provider refuses (an error status, or a 2xx answer
        that is neither a stream nor a finished response) raises an error that names the
        response; it is never polled.
        """
        if live.last_seq is None:
            return None
        resume_url = self.responses_url + "/" + live.response_id
        resume_headers = {
            **live.headers,
            "Accept": "text/event-stream",
            "Accept-Encoding": "identity",
        }
        began = time.monotonic()
        retries = 0
        reason = None
        status_code = None
        while time.monotonic() < live.deadline:
            resp = None
            exc = None
            try:
                self._log_request("GET", resume_url)
                resp = self.session.get(
                    resume_url,
                    params={"stream": "true", "starting_after": live.last_seq},
                    headers=resume_headers,
                    timeout=live.request_timeout,
                    stream=True,
                )
                if not is_event_stream(resp):
                    # A JSON or text answer is read whole here: a body cut on the way is
                    # a transport error to retry, never an exception that escapes the
                    # resume with the response id known.
                    resp.content
            except STREAM_ERRORS as error:
                exc = error
            status = getattr(resp, "status_code", None) if resp is not None else None
            if exc is None and isinstance(status, int) and status < 400:
                if is_event_stream(resp):
                    api_logger.info("[RESP] %s %s (stream)", status, resp.url)
                    if live.traced:
                        live.trace.add_event(
                            "provider_stream_resumed",
                            {
                                "step": live.step,
                                "correlation_id": live.correlation_id,
                                "response_id": live.response_id,
                                "mode": "stream",
                                "starting_after": live.last_seq,
                            },
                        )
                    emit_event(
                        live.on_event,
                        {
                            "kind": "stream_resumed",
                            "mode": "stream",
                            "response_id": live.response_id,
                        },
                    )
                    return resp
                try:
                    self._log_response(resp)
                    data = resp.json()
                except Exception:
                    data = None
                _close(resp)
                if not isinstance(data, dict):
                    self._resume_refused(live, resp, status, unstreamed=True)
                if live.traced:
                    live.trace.add_response(
                        data,
                        step_index=live.step or 1,
                        kind="poll_response",
                        provider_key_id=live.provider_key_id,
                    )
                if (
                    data.get("status") in ("completed", "incomplete", "failed", "cancelled")
                    or data.get("error") is not None
                ):
                    return data
                self._resume_refused(live, resp, status, unstreamed=True)
            if resp is not None and exc is None and not is_event_stream(resp):
                try:
                    self._log_response(resp)
                except Exception:
                    logger.debug("Provider resume answer was not logged", exc_info=True)
            if exc is None and status == 404:
                _close(resp)
                return None
            if exc is None and status in RESUME_UNSUPPORTED_STATUSES:
                self._resume_refused(live, resp, status)
            if exc is not None or status in (408, 425, 429) or (
                isinstance(status, int) and status >= 500
            ):
                retries += 1
                reason = failure_reason(exc, resp)
                status_code = status
                retry_after = (
                    retry_after_seconds(getattr(resp, "headers", None))
                    if resp is not None
                    else None
                )
                _close(resp)
                now = time.monotonic()
                remaining = live.deadline - now
                delay = backoff_delay(retries, now - began, remaining, retry_after)
                if delay is None:
                    break
                note_retry(
                    live.trace,
                    live.on_event,
                    operation="stream",
                    attempt=retries,
                    reason=reason,
                    status_code=status,
                    error_type=type(exc).__name__ if exc is not None else None,
                    delay=delay,
                    retry_after=retry_after,
                    remaining=remaining,
                    response_id=live.response_id,
                    step=live.step,
                )
                time.sleep(delay)
                continue
            self._resume_refused(live, resp, status)
        if retries:
            note_retry_exhausted(
                live.trace,
                operation="stream",
                attempts=retries,
                reason=reason,
                status_code=status_code,
                elapsed=time.monotonic() - began,
                step=live.step,
            )
        return None

    def _resume_refused(self, live, resp, status, unstreamed=False):
        """End a stream whose resume the provider refused, naming the response.

        ``unstreamed`` marks a 2xx answer that was neither a stream nor a finished
        response; any other refusal is an error status. Neither is ever polled.
        """
        detail = _response_detail(resp)
        _close(resp)
        if unstreamed:
            message = (
                f"Provider stream resume for response {live.response_id} "
                f"was answered without a stream (HTTP {status})"
            )
        else:
            message = (
                f"Provider stream resume HTTP {status} for response {live.response_id}"
            )
        if live.traced:
            live.trace.add_event(
                "api_poll_error",
                {
                    "step": live.step,
                    "correlation_id": live.correlation_id,
                    "response_id": live.response_id,
                    "status_code": status,
                    "error": message,
                    "detail": detail,
                },
            )
            live.trace.record_error(
                "yandex_stream",
                message,
                step=live.step,
                error_type="StreamResumeRejected",
            )
        raise YandexClientError(message, status_code=status)

    def _poll_fallback(self, live):
        """Read the stored response by id with a fresh poll budget."""
        if live.traced:
            live.trace.add_event(
                "provider_stream_resumed",
                {
                    "step": live.step,
                    "correlation_id": live.correlation_id,
                    "response_id": live.response_id,
                    "mode": "poll",
                },
            )
        emit_event(
            live.on_event,
            {"kind": "stream_resumed", "mode": "poll", "response_id": live.response_id},
        )
        return self._wait(
            live.response_id,
            timeout=live.poll_timeout,
            execution_trace=live.trace,
            trace_step=live.step,
            headers=live.headers,
            on_event=live.on_event,
        )

    def _keep_stream_partial(self, live):
        """Keep the streamed text as trace evidence, so a failed turn still shows it."""
        if not getattr(live, "traced", False) or not live.response_id or not live.text:
            return
        try:
            live.trace.add_response(
                {
                    "id": live.response_id,
                    "status": "in_progress",
                    "logos_stream_partial": True,
                    "output": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [
                                {"type": "output_text", "text": "".join(live.text)}
                            ],
                        }
                    ],
                },
                step_index=live.step or 1,
                kind="stream_partial",
                provider_key_id=live.provider_key_id,
            )
        except Exception:
            logger.exception("Streamed partial text was not kept in the trace")
