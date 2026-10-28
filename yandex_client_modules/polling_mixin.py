import json
import time
import requests

from yandex_client_modules.errors import YandexClientError
from yandex_client_modules.response_poller import poll_delay_cap
from yandex_client_modules.host import bound


class YandexPollingMixin:
    def _wait(
        self, task_id, timeout=180, execution_trace=None, trace_step=None, headers=None
    ):
        start = time.time()
        url = self.responses_url + "/" + task_id
        delay = 0.5
        last_snapshot = None
        while time.time() - start < timeout:
            try:
                poll_start = time.time()
                # Polls read a request that ask() already admitted and counted. Charging
                # a quota unit per poll could deny the result of a task the provider keeps running.
                self._log_request("GET", url)
                resp = self._log_response(
                    self.session.get(url, headers=headers, timeout=15)
                )
                if resp.status_code == 404:
                    if execution_trace and isinstance(
                        execution_trace, bound.ExecutionTrace
                    ):
                        execution_trace.add_event(
                            "api_poll_error",
                            {
                                "step": trace_step,
                                "correlation_id": execution_trace.get_step_correlation_id(
                                    trace_step or 1
                                ),
                                "response_id": task_id,
                                "status_code": 404,
                            },
                        )
                        execution_trace.record_error(
                            "yandex_poll",
                            "HTTP 404 while polling response",
                            step=trace_step,
                            error_type="PollNotFound",
                        )
                    time.sleep(delay)
                    delay = min(delay * 1.5, poll_delay_cap(time.time() - start))
                    continue
                resp.raise_for_status()
            except requests.RequestException as e:
                status_code = getattr(getattr(e, "response", None), "status_code", None)
                error_message = (
                    f"Provider polling HTTP {status_code}"
                    if status_code is not None
                    else f"Provider polling transport error ({type(e).__name__})"
                )
                if execution_trace and isinstance(
                    execution_trace, bound.ExecutionTrace
                ):
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
                    execution_trace.record_error(
                        "yandex_poll",
                        error_message,
                        step=trace_step,
                        error_type=type(e).__name__,
                    )
                if (
                    isinstance(status_code, int)
                    and 400 <= status_code < 500
                    and status_code not in {404, 408, 429}
                ):
                    raise YandexClientError(
                        error_message, status_code=status_code
                    ) from e
                time.sleep(delay)
                delay = min(delay * 1.5, poll_delay_cap(time.time() - start))
                continue

            invalid_json = False
            try:
                data = resp.json()
            except ValueError:
                # JSONDecodeError also inherits RequestException in requests.
                # Keep the body as trace evidence instead of retrying it as a
                # transport failure; only the fixed envelope reaches callers.
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
            if (
                execution_trace
                and isinstance(execution_trace, bound.ExecutionTrace)
                and snapshot_key != last_snapshot
            ):
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

            status = response.get("status")
            if (
                status in ("completed", "incomplete", "failed", "cancelled")
                or response.get("error") is not None
            ):
                if execution_trace and isinstance(
                    execution_trace, bound.ExecutionTrace
                ):
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
                    ) if execution_trace and isinstance(
                        execution_trace, bound.ExecutionTrace
                    ) else None
                if status == "cancelled":
                    execution_trace.record_error(
                        "yandex_poll",
                        "Task cancelled",
                        step=trace_step,
                        error_type="ProviderTaskCancelled",
                    ) if execution_trace and isinstance(
                        execution_trace, bound.ExecutionTrace
                    ) else None
                # ask() delivers every terminal state to its checkpoint callback
                # and caller; provider failures use the normal diagnostics path.
                return response
            time.sleep(delay)
            delay = min(delay * 1.5, poll_delay_cap(time.time() - start))

        if execution_trace and isinstance(execution_trace, bound.ExecutionTrace):
            execution_trace.add_event(
                "api_poll_timeout",
                {
                    "step": trace_step,
                    "correlation_id": execution_trace.get_step_correlation_id(
                        trace_step or 1
                    ),
                    "response_id": task_id,
                    "timeout": timeout,
                },
            )
        if execution_trace and isinstance(execution_trace, bound.ExecutionTrace):
            execution_trace.record_error(
                "yandex_poll",
                f"Timeout ({timeout}s) waiting for task {task_id}",
                step=trace_step,
                error_type="PollTimeout",
            )
        raise YandexClientError(f"Timeout ({timeout}s) waiting for task {task_id}")
