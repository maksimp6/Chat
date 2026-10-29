"""Polling mixin of the Responses client: waits for background responses to finish."""

import json
import time
import requests

from yandex_client_modules.errors import YandexClientError
from yandex_client_modules.provider_retry import (
    backoff_delay,
    classify_poll,
    failure_reason,
    poll_timeout_text,
    retry_after_seconds,
    retry_after_too_long_text,
)
from yandex_client_modules.response_poller import poll_delay_cap
from yandex_client_modules.host import bound
from yandex_client_modules.stream_mixin import (
    emit_event,
    note_retry,
    note_retry_exhausted,
)

_TERMINAL_STATUSES = ("completed", "incomplete", "failed", "cancelled")


class YandexPollingMixin:
    def _wait(
        self,
        task_id,
        timeout=180,
        execution_trace=None,
        trace_step=None,
        headers=None,
        *,
        on_event=None,
        should_stop=None,
    ):
        # The deadline runs on the monotonic clock: a wall-clock jump on the phone
        # neither ends the wait early nor stretches it.
        start = time.monotonic()
        url = self.responses_url + "/" + task_id
        delay = 0.5
        last_snapshot = None
        traced = execution_trace and isinstance(execution_trace, bound.ExecutionTrace)
        # Only a change of poll error is traced as api_poll_error: an unreachable provider
        # would otherwise add an event for every attempt of the wait. Each retry is a
        # provider_retry_scheduled event; a transient error is not a trace error.
        last_poll_error = None
        last_error_text = None
        failed_polls = 0
        # Consecutive failed polls; the pause after an error grows with it and a
        # successful poll starts it again.
        error_attempts = 0
        last_status = None
        # The failure envelope of the last poll when it was not JSON; it is the result
        # if the wait ends right after it.
        invalid_response = None
        while time.monotonic() - start < timeout:
            if should_stop is not None and should_stop():
                raise YandexClientError(f"Turn stopped by its owner while waiting for {task_id}")
            poll_start = time.time()
            resp = None
            exc = None
            try:
                # Polls read a request that ask() already admitted and counted. Charging
                # a quota unit per poll could deny the result of a task the provider keeps running.
                self._log_request("GET", url)
                resp = self._log_response(
                    self.session.get(url, headers=headers, timeout=15)
                )
            except requests.RequestException as e:
                exc = e
            outcome = classify_poll(exc, resp)

            if outcome == "fatal":
                status_code = resp.status_code
                error_message = f"Provider polling HTTP {status_code}"
                http_error = None
                try:
                    resp.raise_for_status()
                except requests.HTTPError as e:
                    http_error = e
                error_type = (
                    type(http_error).__name__ if http_error is not None else "HTTPError"
                )
                if traced and (error_message, error_type) != last_poll_error:
                    execution_trace.add_event(
                        "api_poll_error",
                        {
                            "step": trace_step,
                            "correlation_id": execution_trace.get_step_correlation_id(
                                trace_step or 1
                            ),
                            "response_id": task_id,
                            "status_code": status_code,
                            "error": error_message,
                        },
                    )
                if traced:
                    execution_trace.record_error(
                        "yandex_poll",
                        error_message,
                        step=trace_step,
                        error_type=error_type,
                    )
                raise YandexClientError(
                    error_message, status_code=status_code
                ) from http_error

            if outcome == "retry":
                invalid_response = None
                failed_polls += 1
                error_attempts += 1
                status_code = resp.status_code if resp is not None else None
                if status_code == 404:
                    error_message = "HTTP 404 while polling response"
                    error_type = "PollNotFound"
                    event = {"status_code": 404}
                elif exc is not None:
                    error_message = (
                        f"Provider polling transport error ({type(exc).__name__})"
                    )
                    error_type = type(exc).__name__
                    event = {"status_code": None, "error": error_message}
                else:
                    error_message = f"Provider polling HTTP {status_code}"
                    error_type = "HTTPError"
                    event = {"status_code": status_code, "error": error_message}
                poll_error = (error_message, error_type)
                if traced and poll_error != last_poll_error:
                    execution_trace.add_event(
                        "api_poll_error",
                        {
                            "step": trace_step,
                            "correlation_id": execution_trace.get_step_correlation_id(
                                trace_step or 1
                            ),
                            "response_id": task_id,
                            **event,
                        },
                    )
                last_poll_error = poll_error
                last_error_text = error_message
                reason = failure_reason(exc, resp)
                retry_after = (
                    retry_after_seconds(getattr(resp, "headers", None))
                    if resp is not None
                    else None
                )
                elapsed = time.monotonic() - start
                pause = backoff_delay(
                    error_attempts, elapsed, timeout - elapsed, retry_after
                )
                if pause is None:
                    note_retry_exhausted(
                        execution_trace,
                        operation="poll",
                        attempts=failed_polls,
                        reason=reason,
                        status_code=status_code,
                        elapsed=elapsed,
                        step=trace_step,
                    )
                    if retry_after is not None and retry_after >= timeout - elapsed:
                        # The provider asked for a pause past the deadline: that, not a
                        # wait that ran out, ends the poll.
                        error_message = (
                            f"Provider asked to poll again in {retry_after:.0f}s, "
                            f"after the {timeout}s wait for task {task_id} ends"
                        )
                        if traced:
                            execution_trace.record_error(
                                "yandex_poll",
                                error_message,
                                step=trace_step,
                                error_type="PollRetryAfterTooLong",
                            )
                        raise YandexClientError(
                            error_message,
                            status_code=status_code,
                            user_text=retry_after_too_long_text(retry_after, timeout),
                        )
                    break
                note_retry(
                    execution_trace,
                    on_event,
                    operation="poll",
                    attempt=error_attempts,
                    reason=reason,
                    status_code=status_code,
                    error_type=error_type,
                    delay=pause,
                    retry_after=retry_after,
                    remaining=timeout - elapsed,
                    response_id=task_id,
                    step=trace_step,
                )
                time.sleep(pause)
                continue

            last_poll_error = None
            invalid_json = False
            try:
                data = resp.json()
            except ValueError:
                # JSONDecodeError also inherits RequestException in requests.
                # Keep the body as trace evidence; the poll is retried, and only the
                # fixed envelope reaches callers if the wait ends on it.
                data = resp.text
                invalid_json = True

            poll_end = time.time()
            snapshot_key = json.dumps(
                data, sort_keys=True, ensure_ascii=False, default=str
            )
            response = (
                data
                if isinstance(data, dict)
                else {
                    "id": task_id,
                    "status": "failed",
                    "error": {
                        "code": "provider_invalid_json"
                        if invalid_json
                        else "provider_invalid_response",
                        "type": "invalid_response",
                    },
                    "output": [],
                }
            )
            if traced and snapshot_key != last_snapshot:
                poll_timing_ms = round((poll_end - poll_start) * 1000, 2)
                execution_trace.add_response(
                    data,
                    step_index=trace_step or 1,
                    start_timestamp=poll_start,
                    end_timestamp=poll_end,
                    timing_ms=poll_timing_ms,
                    kind="poll_response",
                )
                execution_trace.add_event(
                    "api_poll_response_received",
                    {
                        "step": trace_step,
                        "correlation_id": execution_trace.get_step_correlation_id(
                            trace_step or 1
                        ),
                        "response_id": task_id,
                        "status": response.get("status"),
                        "timing_ms": poll_timing_ms,
                        "has_output": bool(response.get("output")),
                        "snapshot_changed": True,
                    },
                )
                last_snapshot = snapshot_key

            if invalid_json:
                failed_polls += 1
                error_attempts += 1
                invalid_response = response
                last_error_text = "Provider polling returned a body that is not JSON"
                elapsed = time.monotonic() - start
                pause = backoff_delay(error_attempts, elapsed, timeout - elapsed)
                if pause is None:
                    note_retry_exhausted(
                        execution_trace,
                        operation="poll",
                        attempts=failed_polls,
                        reason="invalid_json",
                        status_code=resp.status_code,
                        elapsed=elapsed,
                        step=trace_step,
                    )
                    break
                note_retry(
                    execution_trace,
                    on_event,
                    operation="poll",
                    attempt=error_attempts,
                    reason="invalid_json",
                    status_code=resp.status_code,
                    error_type="InvalidJSON",
                    delay=pause,
                    retry_after=None,
                    remaining=timeout - elapsed,
                    response_id=task_id,
                    step=trace_step,
                )
                time.sleep(pause)
                continue

            invalid_response = None
            error_attempts = 0
            status = response.get("status")
            if status != last_status:
                last_status = status
                emit_event(
                    on_event,
                    {"kind": "response_status", "status": status, "response_id": task_id},
                )
            if status in _TERMINAL_STATUSES or response.get("error") is not None:
                return self._poll_terminal(
                    response, task_id, execution_trace, trace_step
                )
            time.sleep(delay)
            delay = min(delay * 1.5, poll_delay_cap(time.monotonic() - start))

        if invalid_response is not None:
            # The wait ended on a body that is not JSON: today's failure envelope, whose
            # raw body is already in the trace.
            return self._poll_terminal(
                invalid_response, task_id, execution_trace, trace_step
            )
        if traced:
            execution_trace.add_event(
                "api_poll_timeout",
                {
                    "step": trace_step,
                    "correlation_id": execution_trace.get_step_correlation_id(
                        trace_step or 1
                    ),
                    "response_id": task_id,
                    "timeout": timeout,
                    "failed_polls": failed_polls,
                    "last_error": last_error_text,
                },
            )
            execution_trace.record_error(
                "yandex_poll",
                f"Timeout ({timeout}s) waiting for task {task_id}",
                step=trace_step,
                error_type="PollTimeout",
            )
        raise YandexClientError(
            f"Timeout ({timeout}s) waiting for task {task_id}",
            user_text=poll_timeout_text(timeout),
        )

    def _poll_terminal(self, response, task_id, execution_trace, trace_step):
        """A terminal poll result, recorded as before; every terminal state is returned."""
        if execution_trace and isinstance(execution_trace, bound.ExecutionTrace):
            status = response.get("status")
            execution_trace.add_event(
                "api_poll_completed",
                {
                    "step": trace_step,
                    "correlation_id": execution_trace.get_step_correlation_id(
                        trace_step or 1
                    ),
                    "response_id": task_id,
                    "status": status,
                },
            )
            if status == "failed":
                execution_trace.record_error(
                    "yandex_poll",
                    "Provider task failed",
                    step=trace_step,
                    error_type="ProviderTaskFailed",
                )
            if status == "cancelled":
                execution_trace.record_error(
                    "yandex_poll",
                    "Task cancelled",
                    step=trace_step,
                    error_type="ProviderTaskCancelled",
                )
        # ask() delivers every terminal state to its checkpoint callback
        # and caller; provider failures use the normal diagnostics path.
        return response
