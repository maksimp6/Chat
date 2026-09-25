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
from provider_quotas import ProviderQuotaExceeded, record_usage, reserve_request


def _resolve_quota_user_id(execution_trace=None):
    if isinstance(execution_trace, ExecutionTrace):
        user_id = (execution_trace.trace.get("context") or {}).get("user_id")
        if user_id:
            return str(user_id).strip()
    try:
        from treasury_identity import get_current_owner_id
        return get_current_owner_id(required=False)
    except Exception:
        return None


def _record_quota_usage(execution_trace, reservation, response):
    if reservation is None or not isinstance(response, dict):
        return
    usage = response.get("usage") or {}
    if not isinstance(usage, dict):
        return
    input_tokens = int(usage.get("input_tokens", 0) or 0)
    output_tokens = int(usage.get("output_tokens", 0) or 0)
    total_tokens = int(usage.get("total_tokens", input_tokens + output_tokens) or 0)
    token_count = max(total_tokens, input_tokens + output_tokens)
    record_usage(reservation, token_count=token_count)
    if isinstance(execution_trace, ExecutionTrace):
        execution_trace.add_event("provider_quota_usage_recorded", {
            "user_id": reservation.user_id,
            "policy": reservation.policy_name,
            "request_number": reservation.reserved_request_number,
            "token_count": token_count,
            "response_id": response.get("id"),
        })


def _resolve_global_provider_credential(client, execution_trace=None):
    """Refresh auth from the deployment-wide credential store for each request."""
    if decrypt_secret is None or encrypt_secret is None:
        raise YandexClientError(
            "Provider credential encryption is unavailable"
        )
    if not os.getenv("ALICE_PROVIDER_CREDENTIAL_KEY", "").strip():
        raise YandexClientError(
            "ALICE_PROVIDER_CREDENTIAL_KEY is required for provider credentials"
        )

    conn = get_conn()
    try:
        credential = resolve_client_credential(
            client._config,
            conn,
            decrypt_secret,
            encrypt_secret,
            provider="yandex",
        )
    finally:
        conn.close()

    if not isinstance(credential.project_id, str) or not credential.project_id.strip():
        raise YandexClientError("Active Yandex credential has no project_id")
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
        quota_user_id = _resolve_quota_user_id(execution_trace)
        try:
            quota_reservation = reserve_request(quota_user_id)
        except ProviderQuotaExceeded as exc:
            if execution_trace and isinstance(execution_trace, ExecutionTrace):
                execution_trace.add_event("provider_quota_denied", exc.to_dict()["error"])
                execution_trace.record_error("provider_quota", str(exc), error_type=exc.code)
            raise
        if execution_trace and isinstance(execution_trace, ExecutionTrace) and quota_reservation is not None:
            execution_trace.add_event("provider_quota_reserved", {
                "user_id": quota_reservation.user_id,
                "policy": quota_reservation.policy_name,
                "request_number": quota_reservation.reserved_request_number,
                "period_reset_at": quota_reservation.period_reset_at,
            })
        yandex_conv_id = self._resolve_yandex_conv_id(conversation_id)

        metadata = (
            execution_trace.get_metadata()
            if execution_trace and isinstance(execution_trace, ExecutionTrace)
            else None
        )

        payload = build_response_payload(
            project_id=credential.project_id,
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
            if status in ("completed", "incomplete"):
                _record_quota_usage(execution_trace, quota_reservation, data)
                return data
            if status in ("failed", "cancelled"):
                raise YandexClientError(f"Task status: {status}")
        result = self._wait(task_id, execution_trace=execution_trace, trace_step=trace_step_number)
        _record_quota_usage(execution_trace, quota_reservation, result)
        return result

