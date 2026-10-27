import time
import copy

import math
import requests
from typing import Any, Callable

from observability.trace_manager import ExecutionTrace
from storage.db import get_store
from provider_accounts.credentials import resolve_client_credential

try:
    from secret_store.credential_crypto import (
        CredentialCryptoError,
        decrypt_secret,
        encrypt_secret,
    )
except ImportError:  # pragma: no cover
    CredentialCryptoError = None
    decrypt_secret = None
    encrypt_secret = None
from yandex_client_modules.request_utils import sanitize_for_log as _sanitize_for_log
from yandex_client_modules.request_builder import (
    build_response_payload,
    ultra_applied_event,
    validate_generation_params,
)
from yandex_client_modules.request_receipt import build_request_manifest
from yandex_client_modules.errors import YandexClientError
from provider_accounts.quotas import (
    ProviderQuotaExceeded,
    record_usage,
    release_request,
    reserve_request,
)


def _transport_timeout(params: dict[str, Any], key: str, default: float, maximum: float) -> float:
    """Validate local transport limits without sending them as provider fields."""
    value = params.get(key, default)
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not 0 < value <= maximum
        or not math.isfinite(value)
    ):
        raise ValueError(f"{key} must be a finite number in (0, {maximum}]")
    return value


def _capture_request_input(
    execution_trace: Any,
    payload: dict[str, Any],
    step: int | None,
    callback: Callable[[dict[str, Any]], Any] | None,
) -> None:
    traced = isinstance(execution_trace, ExecutionTrace)
    if not traced and callback is None:
        return
    manifest = build_request_manifest(payload)
    correlation_id = (
        execution_trace.get_step_correlation_id(step) if traced and step is not None else None
    )
    if traced:
        execution_trace.add_event(
            "api_request_manifest",
            {
                "method": "POST",
                "step": step,
                "correlation_id": correlation_id,
                "manifest": manifest,
            },
        )
    if callback is not None:
        callback(
            {
                "schema_version": 1,
                "capture_stage": "assembled_payload_before_post",
                "trace_id": execution_trace.trace_id if traced else None,
                "step": step,
                "correlation_id": correlation_id,
                "manifest": copy.deepcopy(manifest),
                # This trusted local writer receives a protected original,
                # never an event/export or the tools definitions/headers.
                "data": copy.deepcopy(
                    {
                        name: payload[name]
                        for name in (
                            "input",
                            "instructions",
                            "conversation",
                            "previous_response_id",
                        )
                        if name in payload
                    }
                ),
            }
        )


def _resolve_quota_user_id(execution_trace=None):
    if isinstance(execution_trace, ExecutionTrace):
        user_id = (execution_trace.trace.get("context") or {}).get("user_id")
        if user_id:
            return str(user_id).strip()
    try:
        from economy.treasury_identity import get_current_owner_id

        return get_current_owner_id(required=False)
    except Exception:
        return None


def _usage_count(usage, key, default=0):
    """A provider token count; malformed values count as zero instead of failing the call."""
    value = usage.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    if not math.isfinite(value) or value < 0:
        return 0
    return int(value)


def _record_quota_usage(execution_trace, reservation, response):
    if reservation is None or not isinstance(response, dict):
        return
    usage = response.get("usage") or {}
    if not isinstance(usage, dict):
        return
    input_tokens = _usage_count(usage, "input_tokens")
    output_tokens = _usage_count(usage, "output_tokens")
    total_tokens = _usage_count(usage, "total_tokens", input_tokens + output_tokens)
    token_count = max(total_tokens, input_tokens + output_tokens)
    record_usage(reservation, token_count=token_count)
    if isinstance(execution_trace, ExecutionTrace):
        execution_trace.add_event(
            "provider_quota_usage_recorded",
            {
                "user_id": reservation.user_id,
                "policy": reservation.policy_name,
                "request_number": reservation.reserved_request_number,
                "token_count": token_count,
                "response_id": response.get("id"),
            },
        )


def _resolve_global_provider_credential(client, execution_trace=None):
    """Refresh auth from the deployment-wide credential store for each request."""
    if decrypt_secret is None or encrypt_secret is None:
        raise YandexClientError("Provider credential encryption is unavailable")

    conn = get_store()
    try:
        credential = resolve_client_credential(
            client._config,
            conn,
            decrypt_secret,
            encrypt_secret,
            provider="yandex",
        )
    except CredentialCryptoError as exc:
        # The stored credential is encrypted and its key is not available here.
        raise YandexClientError(
            "Stored provider credential cannot be read on this deployment"
        ) from exc

    if not isinstance(credential.project_id, str) or not credential.project_id.strip():
        raise YandexClientError("Active Yandex credential has no project_id")

    if execution_trace and isinstance(execution_trace, ExecutionTrace):
        execution_trace.set_provider_key(
            credential.trace_key_id,
            fingerprint=credential.fingerprint,
            issued_at=credential.issued_at,
            expires_at=credential.expires_at,
            project_id=credential.project_id,
            source="global",
            key_mask=_key_mask(credential.api_key),
        )
    return credential


def _key_mask(api_key):
    """Show only the last four characters of a provider key, as ****abcd for reports.
    A short key gets a bare **** so the report still shows that a mask applies."""
    if not isinstance(api_key, str) or not api_key:
        return None
    if len(api_key) <= 8:
        return "****"
    return "****" + api_key[-4:]


def provider_request_headers(credential) -> dict[str, str]:
    """Credential headers for one request. The shared session is never mutated, so
    concurrent requests cannot send each other's key or project."""
    return {
        "Authorization": "Api-Key " + credential.api_key,
        "OpenAI-Project": credential.project_id,
    }


class YandexRequestMixin:
    def ask(
        self,
        message,
        model_key,
        conversation_id=None,
        params=None,
        execution_trace=None,
        trace_step=None,
    ):
        params = validate_generation_params(params)
        request_timeout = _transport_timeout(params, "request_timeout", 90, 900)
        poll_timeout = _transport_timeout(params, "poll_timeout", 180, 7200)
        on_provider_response = params.get("on_provider_response")
        if on_provider_response is not None and not callable(on_provider_response):
            raise ValueError("on_provider_response must be callable or None")
        on_provider_input = params.get("on_provider_input")
        if on_provider_input is not None and not callable(on_provider_input):
            raise ValueError("on_provider_input must be callable or None")
        is_background = params.get("background", True)
        if is_background:
            params["store"] = True

        credential = _resolve_global_provider_credential(self, execution_trace)
        headers = provider_request_headers(credential)
        quota_user_id = _resolve_quota_user_id(execution_trace)
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
        url = self.responses_url
        # Photos share Responses input and the existing conversation/tool loop.
        if isinstance(execution_trace, ExecutionTrace):
            execution_trace.add_event("ultra_profile_applied", ultra_applied_event(model_key, params, payload))

        # Reserved after the payload is built, so a request rejected for its parameters uses no quota.
        quota_reservation = None
        try:
            quota_reservation = reserve_request(quota_user_id)
        except ProviderQuotaExceeded as exc:
            if execution_trace and isinstance(execution_trace, ExecutionTrace):
                execution_trace.add_event("provider_quota_denied", exc.to_dict()["error"])
                execution_trace.record_error("provider_quota", str(exc), error_type=exc.code)
            raise
        if (
            execution_trace
            and isinstance(execution_trace, ExecutionTrace)
            and quota_reservation is not None
        ):
            execution_trace.add_event(
                "provider_quota_reserved",
                {
                    "user_id": quota_reservation.user_id,
                    "policy": quota_reservation.policy_name,
                    "request_number": quota_reservation.reserved_request_number,
                    "period_reset_at": quota_reservation.period_reset_at,
                },
            )

        trace_step_number = trace_step
        if isinstance(execution_trace, ExecutionTrace) and trace_step_number is None:
            trace_step_number = len(execution_trace.trace.get("api_requests", [])) + 1

        self._log_request("POST", url, json=payload)
        _capture_request_input(execution_trace, payload, trace_step_number, on_provider_input)
        request_start_timestamp = time.time()
        request_start_perf = time.perf_counter()
        if execution_trace and isinstance(execution_trace, ExecutionTrace):
            execution_trace.add_api_request(
                _sanitize_for_log(payload),
                step_index=trace_step_number,
                start_timestamp=request_start_timestamp,
                provider_key_id=credential.trace_key_id,
            )
            execution_trace.add_event(
                "api_request_sent",
                {
                    "method": "POST",
                    "url": url,
                    "step": trace_step_number,
                    "correlation_id": execution_trace.get_step_correlation_id(trace_step_number),
                    "payload": _sanitize_for_log(payload),
                },
            )

        resp = None
        try:
            resp = self._log_response(
                self.session.post(
                    url, json=payload, headers=headers, timeout=request_timeout
                )
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            # No answer was produced for the reserved request, so it does not count against the quota.
            release_request(quota_reservation)
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
                execution_trace.add_event(
                    "api_request_error",
                    {
                        "method": "POST",
                        "url": url,
                        "step": trace_step_number,
                        "correlation_id": execution_trace.get_step_correlation_id(
                            trace_step_number
                        ),
                        "model": payload.get("model"),
                        "status_code": status_code,
                        "error": error_message,
                        "detail": error_detail,
                        "start_timestamp": request_start_timestamp,
                        "end_timestamp": error_timestamp,
                        "timing_ms": round((time.perf_counter() - request_start_perf) * 1000, 2),
                    },
                )

            raise YandexClientError(error_message, status_code=status_code) from e

        request_end_timestamp = time.time()
        request_duration_ms = round((time.perf_counter() - request_start_perf) * 1000, 2)

        try:
            data = resp.json()
        except ValueError as e:
            # A 200 answer without JSON is not a usable reply, so the reserved request is not charged.
            release_request(quota_reservation)
            if execution_trace and isinstance(execution_trace, ExecutionTrace):
                execution_trace.add_event(
                    "api_response_invalid_json",
                    {
                        "method": "POST",
                        "url": url,
                        "step": trace_step_number,
                        "model": payload.get("model"),
                        "status_code": resp.status_code,
                        "error": str(e),
                        "start_timestamp": request_start_timestamp,
                        "end_timestamp": request_end_timestamp,
                    },
                )
            raise YandexClientError(
                "Provider returned a response that is not valid JSON",
                status_code=resp.status_code,
            ) from e
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
            execution_trace.add_event(
                "api_request_completed",
                {
                    "method": "POST",
                    "url": url,
                    "step": step,
                    "correlation_id": execution_trace.get_step_correlation_id(step),
                    "start_timestamp": request_start_timestamp,
                    "end_timestamp": request_end_timestamp,
                    "timing_ms": request_duration_ms,
                    "response_id": data.get("id") if isinstance(data, dict) else None,
                    "status": data.get("status") if isinstance(data, dict) else None,
                },
            )
        if not isinstance(data, dict):
            # add_response above retains the original JSON as evidence. Callers
            # and checkpoints receive a safe failure object, never a scalar.
            data = {
                "status": "failed",
                "error": {"code": "provider_invalid_response", "type": "invalid_response"},
                "output": [],
            }
        if on_provider_response is not None:
            on_provider_response(data)
        task_id = data.get("id")
        status = data.get("status")
        if status in ("completed", "incomplete", "failed", "cancelled") or data.get("error") is not None:
            _record_quota_usage(execution_trace, quota_reservation, data)
            return data
        if not task_id:
            raise YandexClientError("Provider response has no task id to poll")
        wait_params = {"timeout": poll_timeout} if "poll_timeout" in params else {}
        result = self._wait(
            task_id,
            execution_trace=execution_trace,
            trace_step=trace_step_number,
            headers=headers,
            **wait_params,
        )
        if on_provider_response is not None:
            on_provider_response(result)
        _record_quota_usage(execution_trace, quota_reservation, result)
        return result

