import json
import time
import requests

from trace_manager import ExecutionTrace
from yandex_client_modules.errors import YandexClientError
from provider_quotas import ProviderQuotaExceeded, reserve_request


class YandexPollingMixin:
    def _wait(self, task_id, timeout=180, execution_trace=None, trace_step=None):
        start = time.time()
        url = self.responses_url + "/" + task_id
        delay = 0.5
        last_snapshot = None
        while time.time() - start < timeout:
            try:
                poll_start = time.time()
                quota_reservation = None
                if isinstance(execution_trace, ExecutionTrace):
                    quota_user_id = (execution_trace.trace.get("context") or {}).get("user_id")
                    try:
                        quota_reservation = reserve_request(quota_user_id)
                    except ProviderQuotaExceeded as exc:
                        execution_trace.add_event("provider_quota_denied", {
                            "reason": exc.reason,
                            "user_id": exc.user_id,
                            "request_type": "poll",
                            "response_id": task_id,
                        })
                        execution_trace.record_error(
                            "provider_quota",
                            str(exc),
                            step=trace_step,
                            error_type=exc.code,
                        )
                        raise
                    if quota_reservation is not None:
                        execution_trace.add_event("provider_quota_reserved", {
                            "user_id": quota_reservation.user_id,
                            "policy": quota_reservation.policy_name,
                            "request_number": quota_reservation.reserved_request_number,
                            "request_type": "poll",
                            "period_reset_at": quota_reservation.period_reset_at,
                        })
                self._log_request("GET", url)
                resp = self._log_response(self.session.get(url, timeout=15))
                if resp.status_code == 404:
                    if execution_trace and isinstance(execution_trace, ExecutionTrace):
                        execution_trace.add_event("api_poll_error", {
                            "step": trace_step,
                            "correlation_id": execution_trace.get_step_correlation_id(trace_step or 1),
                            "response_id": task_id,
                            "status_code": 404
                        })
                        execution_trace.record_error(
                            "yandex_poll",
                            "HTTP 404 while polling response",
                            step=trace_step,
                            error_type="PollNotFound",
                        )
                    time.sleep(delay)
                    delay = min(delay * 1.5, 3)
                    continue
                resp.raise_for_status()
                data = resp.json()
            except requests.RequestException as e:
                if execution_trace and isinstance(execution_trace, ExecutionTrace):
                    execution_trace.add_event("api_poll_error", {
                        "step": trace_step,
                        "correlation_id": execution_trace.get_step_correlation_id(trace_step or 1),
                        "response_id": task_id,
                        "error": str(e)
                    })
                    execution_trace.record_error(
                        "yandex_poll",
                        str(e),
                        step=trace_step,
                        error_type=type(e).__name__,
                    )
                time.sleep(delay)
                delay = min(delay * 1.5, 3)
                continue

            poll_end = time.time()
            snapshot_key = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
            if execution_trace and isinstance(execution_trace, ExecutionTrace) and snapshot_key != last_snapshot:
                poll_timing_ms = round((poll_end - poll_start) * 1000, 2)
                execution_trace.add_response(
                    data,
                    step_index=trace_step or 1,
                    start_timestamp=poll_start,
                    end_timestamp=poll_end,
                    timing_ms=poll_timing_ms,
                    kind="poll_response"
                )
                execution_trace.add_event("api_poll_response_received", {
                    "step": trace_step,
                    "correlation_id": execution_trace.get_step_correlation_id(trace_step or 1),
                    "response_id": task_id,
                    "status": data.get("status"),
                    "timing_ms": poll_timing_ms,
                    "has_output": bool(data.get("output")),
                    "snapshot_changed": True
                })
                last_snapshot = snapshot_key

            status = data.get("status")
            if status in ("completed", "incomplete", "failed", "cancelled"):
                if execution_trace and isinstance(execution_trace, ExecutionTrace):
                    execution_trace.add_event("api_poll_completed", {
                        "step": trace_step,
                        "correlation_id": execution_trace.get_step_correlation_id(trace_step or 1),
                        "response_id": task_id,
                        "status": status
                    })
                if status == "failed":
                    err = data.get('error')
                    err_msg = err.get('message', 'unknown') if isinstance(err, dict) else str(err)
                    execution_trace.record_error(
                        "yandex_poll",
                        err_msg,
                        step=trace_step,
                        error_type="ProviderTaskFailed",
                    ) if execution_trace and isinstance(execution_trace, ExecutionTrace) else None
                    raise YandexClientError(f"Task failed: {err_msg}")
                if status == "cancelled":
                    execution_trace.record_error(
                        "yandex_poll",
                        "Task cancelled",
                        step=trace_step,
                        error_type="ProviderTaskCancelled",
                    ) if execution_trace and isinstance(execution_trace, ExecutionTrace) else None
                    raise YandexClientError("Task cancelled")
                return data
            time.sleep(delay)
            delay = min(delay * 1.5, 3)

        if execution_trace and isinstance(execution_trace, ExecutionTrace):
            execution_trace.add_event("api_poll_timeout", {
                "step": trace_step,
                "correlation_id": execution_trace.get_step_correlation_id(trace_step or 1),
                "response_id": task_id,
                "timeout": timeout
            })
        if execution_trace and isinstance(execution_trace, ExecutionTrace):
            execution_trace.record_error(
                "yandex_poll",
                f"Timeout ({timeout}s) waiting for task {task_id}",
                step=trace_step,
                error_type="PollTimeout",
            )
        raise YandexClientError(f"Timeout ({timeout}s) waiting for task {task_id}")
