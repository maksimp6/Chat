"""Conversation mixin of the Responses client: creates Yandex conversations."""

import logging
import math
import time
import uuid
import requests

from yandex_client_modules.errors import YandexClientError
from yandex_client_modules.provider_retry import (
    backoff_delay,
    classify_post,
    conversation_failed_text,
    failure_reason,
    retry_after_seconds,
)
from yandex_client_modules.stream_mixin import note_retry, note_retry_exhausted

api_logger = logging.getLogger("logos.yandex_api")


class YandexConversationMixin:
    def create_conversation(
        self, execution_trace=None, *, retry_timeout=None, on_event=None
    ):
        """Create the provider conversation of a chat.

        ``retry_timeout`` None sends one request. A number retries every transient
        failure until that many seconds after the first attempt (0: no deadline).
        An empty conversation is free and the chat keeps the first id stored, so a
        request whose outcome is unknown is sent again too.
        """
        if retry_timeout is not None:
            return self._create_conversation_retrying(
                execution_trace, retry_timeout, on_event
            )
        try:
            from yandex_client_modules.request_mixin import (
                _resolve_global_provider_credential,
                provider_request_headers,
            )

            credential = _resolve_global_provider_credential(self, execution_trace)
            headers = provider_request_headers(credential)
            self._log_request("POST", self.conversations_url, json={})
            resp = self._log_response(
                self.session.post(
                    self.conversations_url, json={}, headers=headers, timeout=10
                )
            )
            resp.raise_for_status()
            data = resp.json()
            return self._conversation_created(data, execution_trace)
        except requests.RequestException as exc:
            raise YandexClientError("Create conversation: " + str(exc)) from exc

    def add_conversation_items(self, provider_id, items, execution_trace=None):
        """Append message items to a provider conversation, twenty items per request."""
        from yandex_client_modules.request_mixin import (
            _resolve_global_provider_credential,
            provider_request_headers,
        )

        credential = _resolve_global_provider_credential(self, execution_trace)
        headers = provider_request_headers(credential)
        url = f"{self.conversations_url}/{provider_id}/items"
        try:
            for start in range(0, len(items), 20):
                chunk = {"items": items[start : start + 20]}
                self._log_request("POST", url, json=chunk)
                resp = self._log_response(
                    self.session.post(url, json=chunk, headers=headers, timeout=30)
                )
                resp.raise_for_status()
        except requests.RequestException as exc:
            raise YandexClientError("Add conversation items: " + str(exc)) from exc

    def _conversation_created(self, data, execution_trace):
        provider_id = data.get("id") if isinstance(data, dict) else None
        if not isinstance(provider_id, str) or not provider_id.strip():
            raise YandexClientError(
                "Yandex conversation creation returned no provider ID"
            )
        try:
            provider_id = str(uuid.UUID(provider_id))
        except (ValueError, AttributeError) as exc:
            raise YandexClientError(
                "Yandex conversation ID is not a valid UUID"
            ) from exc
        data["id"] = provider_id
        if execution_trace is not None:
            execution_trace.add_event(
                "conversation_created",
                {
                    "provider_conversation_id": provider_id,
                },
            )
        api_logger.info("[CONV] Created: id=%s", provider_id)
        return data

    def _create_conversation_retrying(self, execution_trace, retry_timeout, on_event):
        from yandex_client_modules.request_mixin import (
            _resolve_global_provider_credential,
            provider_request_headers,
        )

        credential = _resolve_global_provider_credential(self, execution_trace)
        headers = provider_request_headers(credential)
        first = time.monotonic()
        deadline = first + retry_timeout if retry_timeout > 0 else None
        attempt = 0
        while True:
            resp = None
            exc = None
            data = None
            try:
                self._log_request("POST", self.conversations_url, json={})
                resp = self._log_response(
                    self.session.post(
                        self.conversations_url, json={}, headers=headers, timeout=10
                    )
                )
            except requests.RequestException as error:
                exc = error
            outcome = classify_post(exc, resp, False)
            reason = None
            if outcome == "ok":
                try:
                    data = resp.json()
                except ValueError:
                    reason = "invalid_json"
                else:
                    return self._conversation_created(data, execution_trace)
            elif outcome == "fatal":
                try:
                    resp.raise_for_status()
                except requests.HTTPError as error:
                    raise YandexClientError(
                        "Create conversation: " + str(error),
                        status_code=resp.status_code,
                    ) from error
                raise YandexClientError(
                    f"Create conversation: HTTP {resp.status_code}",
                    status_code=resp.status_code,
                )
            if outcome == "ambiguous" or reason == "invalid_json":
                # The provider may already have created the conversation, so a second
                # POST could create another one: an unknown outcome is not retried (#1682).
                unknown_reason = reason or failure_reason(exc, resp)
                unknown_status = resp.status_code if resp is not None else None
                raise YandexClientError(
                    "Create conversation: outcome unknown ("
                    + (str(exc) if exc is not None else f"HTTP {unknown_status}" if unknown_status else unknown_reason)
                    + ")",
                    status_code=unknown_status
                    if unknown_status is not None and unknown_status >= 400
                    else None,
                    user_text=conversation_failed_text(
                        time.monotonic() - first, unknown_reason
                    ),
                ) from exc
            # not_sent or a transient refusal (408, 425, 429, 503): sent again.
            attempt += 1
            reason = reason or failure_reason(exc, resp)
            status_code = resp.status_code if resp is not None else None
            retry_after = (
                retry_after_seconds(getattr(resp, "headers", None))
                if resp is not None
                else None
            )
            now = time.monotonic()
            remaining = deadline - now if deadline is not None else math.inf
            pause = backoff_delay(attempt, now - first, remaining, retry_after)
            if pause is None:
                note_retry_exhausted(
                    execution_trace,
                    operation="conversation",
                    attempts=attempt,
                    reason=reason,
                    status_code=status_code,
                    elapsed=now - first,
                )
                cause = (
                    str(exc)
                    if exc is not None
                    else f"HTTP {status_code}"
                    if status_code is not None and status_code >= 400
                    else reason
                )
                raise YandexClientError(
                    "Create conversation: " + cause,
                    status_code=status_code
                    if status_code is not None and status_code >= 400
                    else None,
                    user_text=conversation_failed_text(now - first, reason),
                ) from exc
            note_retry(
                execution_trace,
                on_event,
                operation="conversation",
                attempt=attempt,
                reason=reason,
                status_code=status_code,
                error_type=type(exc).__name__ if exc is not None else None,
                delay=pause,
                retry_after=retry_after,
                remaining=remaining,
            )
            api_logger.info(
                "[CONV] Retry in %.1f s (%s, attempt %s)", pause, reason, attempt
            )
            time.sleep(pause)

    def _resolve_yandex_conv_id(self, conv_id):
        if not conv_id:
            return None
        try:
            return str(uuid.UUID(str(conv_id)))
        except (ValueError, TypeError, AttributeError) as exc:
            raise YandexClientError(
                "Conversation ID must be the Yandex provider UUID"
            ) from exc
