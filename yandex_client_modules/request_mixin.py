import time

import os
import requests

from trace_manager import ExecutionTrace
from db import get_conn
from provider_credentials import resolve_client_credential
try:
    from credential_crypto import decrypt_secret, encrypt_secret
except ImportError:  # pragma: no cover
    decrypt_secret = None
    encrypt_secret = None
from yandex_request_utils import sanitize_for_log as _sanitize_for_log
from yandex_request_builder import build_response_payload
from yandex_client_modules.errors import YandexClientError


def _resolve_global_provider_credential(client, execution_trace=None):
    """Refresh auth from the deployment-wide credential store for each request."""
    conn = get_conn()
    try:
        crypto_key = os.getenv("ALICE_PROVIDER_CREDENTIAL_KEY")
        if decrypt_secret is None or not crypto_key:
            credential = resolve_client_credential(client._config)
        else:
            credential = resolve_client_credential(
                client._config,
                conn,
                decrypt_secret,
                encrypt_secret,
            )
    finally:
        conn.close()

    client.session.headers.update({
        "Authorization": "Api-Key " + credential.api_key,
        "OpenAI-Project": credential.project_id,
    })

    if execution_trace and isinstance(execution_trace, ExecutionTrace):
        execution_trace.set_provider_key(
            credential.trace_key_id,
            fingerprint=credential.fingerprint,
            issued_at=credential.issued_at,
            expires_at=credential.expires_at,
            project_id=credential.project_id,
            source="global",
        )
    return credential


class YandexRequestMixin:

    def ask(self, message, model_key, conversation_id=None, params=None, execution_trace=None, trace_step=None):
        params = params or {}
        is_background = params.get("background", True)
        is_stream = params.get("stream", False)
        if is_background: params["store"] = True
        
        credential = _resolve_global_provider_credential(self, execution_trace)
        yandex_conv_id = self._resolve_yandex_conv_id(conversation_id)

        metadata = (
            execution_trace.get_metadata()
            if execution_trace and isinstance(execution_trace, ExecutionTrace)
            else None
        )

        payload = build_response_payload(
            project_id=self._config.PROJECT_ID,
            model_key=model_key,
            message=message,
            params=params,
            metadata=metadata,
            conversation_id=conversation_id,
            yandex_conv_id=yandex_conv_id,
        )

        request_start_timestamp = time.time()
        request_start_perf = time.perf_counter()
        trace_step_number = trace_step

        if execution_trace and isinstance(execution_trace, ExecutionTrace):
            if trace_step_number is None:
                trace_step_number = len(execution_trace.trace.get("api_requests", [])) + 1
            execution_trace.add_api_request(
                _sanitize_for_log(payload),
                step_index=trace_step_number,
                start_timestamp=request_start_timestamp,
                provider_key_id=credential.trace_key_id,
            )
            execution_trace.add_event("api_request_sent", {
                "method": "POST",
                "url": self.responses_url,
                "step": trace_step_number,
                "correlation_id": execution_trace.get_step_correlation_id(trace_step_number),
                "payload": _sanitize_for_log(payload)
            })

        self._log_request("POST", self.responses_url, json=payload)
        resp = None
        try:
            resp = self._log_response(self.session.post(self.responses_url, json=payload, timeout=90))
            resp.raise_for_status()
        except requests.RequestException as e:
            error_timestamp = time.time()
            status_code = resp.status_code if resp is not None else None
            error_detail = None
            if resp is not None:
                try:
                    error_body = resp.json()
                    error_detail = _sanitize_for_log(error_body)
                except ValueError:
                    error_detail = resp.text[:4000]

            error_message = str(e)
            if status_code is not None:
                error_message = f"HTTP {status_code}: {error_message}"

            if execution_trace and isinstance(execution_trace, ExecutionTrace):
                execution_trace.add_event("api_request_error", {
                    "method": "POST",
                    "url": self.responses_url,
                    "step": trace_step_number,
                    "correlation_id": execution_trace.get_step_correlation_id(trace_step_number),
                    "model": payload.get("model"),
                    "status_code": status_code,
                    "error": error_message,
                    "detail": error_detail,
                    "start_timestamp": request_start_timestamp,
                    "end_timestamp": error_timestamp,
                    "timing_ms": round((time.perf_counter() - request_start_perf) * 1000, 2)
                })

            raise YandexClientError(error_message, status_code=status_code) from e

        request_end_timestamp = time.time()
        request_duration_ms = round(
            (time.perf_counter() - request_start_perf) * 1000, 2
        )

        data = resp.json()

        if execution_trace and isinstance(execution_trace, ExecutionTrace):
            execution_trace.add_response(
                data,
                step_index=trace_step_number or 1,
                start_timestamp=request_start_timestamp,
                end_timestamp=request_end_timestamp,
                timing_ms=request_duration_ms,
                kind="initial_response",
                provider_key_id=credential.trace_key_id,
            )
            step = trace_step
            if step is None:
                api_requests = execution_trace.trace.get("api_requests", [])
                step = api_requests[-1].get("step") if api_requests else 1
            execution_trace.add_event("api_request_completed", {
                "method": "POST",
                "url": self.responses_url,
                "step": step,
                "correlation_id": execution_trace.get_step_correlation_id(step),
                "start_timestamp": request_start_timestamp,
                "end_timestamp": request_end_timestamp,
                "timing_ms": request_duration_ms,
                "response_id": data.get("id"),
                "status": data.get("status")
            })
        task_id = data.get("id")
        if not is_background:
            status = data.get("status")
            if status in ("completed", "incomplete"): return data
            if status in ("failed", "cancelled"): raise YandexClientError(f"Task status: {status}")
        return self._wait(task_id, execution_trace=execution_trace, trace_step=trace_step_number)

