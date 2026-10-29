"""Request mixin of the Responses client: credentials, per-user quotas, transport timeouts, request capture and ask()."""

import time
import copy

import math
from typing import Any, Callable


from yandex_client_modules.request_utils import sanitize_for_log as _sanitize_for_log
from yandex_client_modules.request_builder import (
    build_response_payload,
    ultra_applied_event,
    validate_generation_params,
)
from yandex_client_modules.request_receipt import build_model_view, build_request_manifest
from yandex_client_modules.errors import YandexClientError
from yandex_client_modules.stream_mixin import (
    LiveStream,
    emit_event,
)
from yandex_client_modules.host import (
    bound,
    get_current_owner_id,
    get_store,
    record_usage,
    release_request,
    reserve_request,
    resolve_client_credential,
)


def _transport_timeout(
    params: dict[str, Any], key: str, default: float, maximum: float
) -> float:
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
    traced = isinstance(execution_trace, bound.ExecutionTrace)
    if not traced and callback is None:
        return
    manifest = build_request_manifest(payload)
    correlation_id = (
        execution_trace.get_step_correlation_id(step)
        if traced and step is not None
        else None
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
        # What the model is shown by this request, readable in the reply's details.
        execution_trace.add_event(
            "model_view", {"step": step, "view": build_model_view(payload)}
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
    if isinstance(execution_trace, bound.ExecutionTrace):
        user_id = (execution_trace.trace.get("context") or {}).get("user_id")
        if user_id:
            return str(user_id).strip()
    try:
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
    if isinstance(execution_trace, bound.ExecutionTrace):
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
    if bound.decrypt_secret is None or bound.encrypt_secret is None:
        raise YandexClientError("Provider credential encryption is unavailable")

    conn = get_store()
    try:
        credential = resolve_client_credential(
            client._config,
            conn,
            bound.decrypt_secret,
            bound.encrypt_secret,
            provider="yandex",
        )
    except bound.CredentialCryptoError as exc:
        # The stored credential is encrypted and its key is not available here.
        raise YandexClientError(
            "Stored provider credential cannot be read on this deployment"
        ) from exc

    if not isinstance(credential.project_id, str) or not credential.project_id.strip():
        raise YandexClientError("Active Yandex credential has no project_id")

    if execution_trace and isinstance(execution_trace, bound.ExecutionTrace):
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


# Public names for other units; they reach them through their host ports.
resolve_global_provider_credential = _resolve_global_provider_credential
resolve_quota_user_id = _resolve_quota_user_id
record_quota_usage = _record_quota_usage
transport_timeout = _transport_timeout


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
        # A live chat turn receives normalized progress events; nothing else is sent to it.
        on_stream_event = params.get("on_stream_event")
        if on_stream_event is not None and not callable(on_stream_event):
            raise ValueError("on_stream_event must be callable or None")
        is_background = params.get("background", True)
        if is_background:
            params["store"] = True

        credential = _resolve_global_provider_credential(self, execution_trace)
        headers = provider_request_headers(credential)
        quota_user_id = _resolve_quota_user_id(execution_trace)
        yandex_conv_id = self._resolve_yandex_conv_id(conversation_id)

        metadata = (
            execution_trace.get_metadata()
            if execution_trace and isinstance(execution_trace, bound.ExecutionTrace)
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
        if isinstance(execution_trace, bound.ExecutionTrace):
            execution_trace.add_event(
                "ultra_profile_applied", ultra_applied_event(model_key, params, payload)
            )

        # Reserved after the payload is built, so a request rejected for its parameters uses no quota.
        quota_reservation = None
        try:
            quota_reservation = reserve_request(quota_user_id)
        except bound.ProviderQuotaExceeded as exc:
            if execution_trace and isinstance(execution_trace, bound.ExecutionTrace):
                execution_trace.add_event(
                    "provider_quota_denied", exc.to_dict()["error"]
                )
                execution_trace.record_error(
                    "provider_quota", str(exc), error_type=exc.code
                )
            raise
        try:
            if (
                execution_trace
                and isinstance(execution_trace, bound.ExecutionTrace)
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
            if (
                isinstance(execution_trace, bound.ExecutionTrace)
                and trace_step_number is None
            ):
                trace_step_number = len(execution_trace.trace.get("api_requests", [])) + 1
        except BaseException:
            # Reserved but not yet sent: the quota reservation is returned.
            release_request(quota_reservation)
            raise

        try:
            self._log_request("POST", url, json=payload)
            _capture_request_input(
                execution_trace, payload, trace_step_number, on_provider_input
            )
            request_start_timestamp = time.time()
            request_start_perf = time.perf_counter()
            if execution_trace and isinstance(execution_trace, bound.ExecutionTrace):
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
                        "correlation_id": execution_trace.get_step_correlation_id(
                            trace_step_number
                        ),
                    },
                )
        except BaseException:
            # A local failure before the POST means the provider never received the request,
            # so its quota reservation is returned.
            release_request(quota_reservation)
            raise

        try:
            resp = self._post_response(
                url,
                payload,
                headers,
                request_timeout=request_timeout,
                poll_timeout=poll_timeout,
                trace=execution_trace,
                step=trace_step_number,
                on_event=on_stream_event,
                request_start_timestamp=request_start_timestamp,
                request_start_perf=request_start_perf,
            )
        except YandexClientError as exc:
            # Released only when the POST is known not to have been accepted: it never
            # reached the provider, or a rate limit stayed until the deadline. Any other
            # failure after the request was sent may have been accepted, so it keeps the
            # reservation.
            if getattr(exc, "request_not_accepted", False):
                release_request(quota_reservation)
            raise

        if isinstance(resp, LiveStream):
            # From here on the provider may have accepted the request: a failure of the
            # stream, a resume or a poll, or a failed answer, keeps the reservation.
            result = self._finish_stream(
                resp,
                url=url,
                headers=headers,
                request_timeout=request_timeout,
                poll_timeout=poll_timeout,
                trace=execution_trace,
                step=trace_step_number,
                on_event=on_stream_event,
                on_provider_response=on_provider_response,
                request_start_timestamp=request_start_timestamp,
                request_start_perf=request_start_perf,
                provider_key_id=credential.trace_key_id,
            )
            if on_provider_response is not None:
                on_provider_response(result)
            _record_quota_usage(execution_trace, quota_reservation, result)
            return result

        request_end_timestamp = time.time()
        request_duration_ms = round(
            (time.perf_counter() - request_start_perf) * 1000, 2
        )

        try:
            data = resp.json()
        except ValueError as e:
            # A 200 answer means the provider took the request, so the reservation stays.
            if execution_trace and isinstance(execution_trace, bound.ExecutionTrace):
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
        if execution_trace and isinstance(execution_trace, bound.ExecutionTrace):
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
                "error": {
                    "code": "provider_invalid_response",
                    "type": "invalid_response",
                },
                "output": [],
            }
        if on_provider_response is not None:
            on_provider_response(data)
        emit_event(
            on_stream_event,
            {
                "kind": "response_status",
                "status": data.get("status"),
                "response_id": data.get("id"),
            },
        )
        task_id = data.get("id")
        status = data.get("status")
        if (
            status in ("completed", "incomplete", "failed", "cancelled")
            or data.get("error") is not None
        ):
            _record_quota_usage(execution_trace, quota_reservation, data)
            return data
        if not task_id:
            raise YandexClientError("Provider response has no task id to poll")
        wait_params = {"timeout": poll_timeout} if "poll_timeout" in params else {}
        emit_event(on_stream_event, {"kind": "polling", "response_id": task_id})
        result = self._wait(
            task_id,
            execution_trace=execution_trace,
            trace_step=trace_step_number,
            headers=headers,
            on_event=on_stream_event,
            **wait_params,
        )
        if on_provider_response is not None:
            on_provider_response(result)
        _record_quota_usage(execution_trace, quota_reservation, result)
        return result
