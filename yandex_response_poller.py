"""Polling helper for background Yandex Responses API tasks."""

import json
import time


def wait_for_response(
    *,
    task_id,
    responses_url,
    session,
    log_request,
    log_response,
    error_cls,
    timeout=180,
    execution_trace=None,
    trace_step=None,
    trace_type=None,
):
    """Poll a background response until it reaches a terminal state."""
    start = time.time()
    url = responses_url + "/" + task_id
    delay = 0.5
    last_snapshot = None

    while time.time() - start < timeout:
        poll_start = time.time()
        try:
            log_request("GET", url)
            resp = log_response(session.get(url, timeout=15))
            if resp.status_code == 404:
                if execution_trace is not None:
                    execution_trace.add_event("api_poll_error", {
                        "step": trace_step,
                        "response_id": task_id,
                        "status_code": 404,
                    })
                time.sleep(delay)
                delay = min(delay * 1.5, 3)
                continue
            resp.raise_for_status()
            data = resp.json()
        except error_cls:
            raise
        except (OSError, ValueError) as exc:
            if execution_trace is not None:
                execution_trace.add_event("api_poll_error", {
                    "step": trace_step,
                    "response_id": task_id,
                    "error": str(exc),
                })
            time.sleep(delay)
            delay = min(delay * 1.5, 3)
            continue

        poll_end = time.time()
        snapshot_key = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
        if execution_trace is not None and snapshot_key != last_snapshot:
            execution_trace.add_response(
                data,
                step_index=trace_step or 1,
                start_timestamp=poll_start,
                end_timestamp=poll_end,
                timing_ms=round((poll_end - poll_start) * 1000, 2),
                kind="poll_response",
            )
            last_snapshot = snapshot_key

        status = data.get("status")
        if status in ("completed", "incomplete", "failed", "cancelled"):
            if execution_trace is not None:
                execution_trace.add_event("api_poll_completed", {
                    "step": trace_step,
                    "response_id": task_id,
                    "status": status,
                })
            if status == "failed":
                err = data.get("error")
                err_msg = err.get("message", "unknown") if isinstance(err, dict) else str(err)
                raise error_cls(f"Task failed: {err_msg}")
            if status == "cancelled":
                raise error_cls("Task cancelled")
            return data

        time.sleep(delay)
        delay = min(delay * 1.5, 3)

    if execution_trace is not None:
        execution_trace.add_event("api_poll_timeout", {
            "step": trace_step,
            "response_id": task_id,
            "timeout": timeout,
        })
    raise error_cls(f"Timeout ({timeout}s) waiting for task {task_id}")
