import json
import time
import requests

from trace_manager import ExecutionTrace
from yandex_client_modules.errors import YandexClientError


class YandexPollingMixin:
    def _wait(self, task_id, timeout=180, execution_trace=None, trace_step=None):
        start = time.time()
        url = self.responses_url + "/" + task_id
        delay = 0.5
        last_snapshot = None
        while time.time() - start < timeout:
            try:
                poll_start = time.time()
                self._log_request("GET", url)
                resp = self._log_response(self.session.get(url, timeout=15))
                if resp.status_code == 404:
                    if execution_trace and isinstance(execution_trace, ExecutionTrace):
                        execution_trace.add_event("api_poll_error", {
                            "step": trace_step, "response_id": task_id, "status_code": 404
                        })
                    time.sleep(delay)
                    delay = min(delay * 1.5, 3)
                    continue
                resp.raise_for_status()
                data = resp.json()
            except requests.RequestException as e:
                if execution_trace and isinstance(execution_trace, ExecutionTrace):
                    execution_trace.add_event("api_poll_error", {
                        "step": trace_step, "response_id": task_id, "error": str(e)
                    })
                time.sleep(delay)
                delay = min(delay * 1.5, 3)
                continue

            poll_end = time.time()
            snapshot_key = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
            if execution_trace and isinstance(execution_trace, ExecutionTrace) and snapshot_key != last_snapshot:
                execution_trace.add_response(
                    data,
                    step_index=trace_step or 1,
                    start_timestamp=poll_start,
                    end_timestamp=poll_end,
                    timing_ms=round((poll_end - poll_start) * 1000, 2),
                    kind="poll_response"
                )
                last_snapshot = snapshot_key

            status = data.get("status")
            if status in ("completed", "incomplete", "failed", "cancelled"):
                if execution_trace and isinstance(execution_trace, ExecutionTrace):
                    execution_trace.add_event("api_poll_completed", {
                        "step": trace_step, "response_id": task_id, "status": status
                    })
                if status == "failed":
                    err = data.get('error')
                    err_msg = err.get('message', 'unknown') if isinstance(err, dict) else str(err)
                    raise YandexClientError(f"Task failed: {err_msg}")
                if status == "cancelled":
                    raise YandexClientError("Task cancelled")
                return data
            time.sleep(delay)
            delay = min(delay * 1.5, 3)

        if execution_trace and isinstance(execution_trace, ExecutionTrace):
            execution_trace.add_event("api_poll_timeout", {
                "step": trace_step, "response_id": task_id, "timeout": timeout
            })
        raise YandexClientError(f"Timeout ({timeout}s) waiting for task {task_id}")
