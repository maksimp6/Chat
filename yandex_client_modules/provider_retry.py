"""Retry decisions for provider requests: failure classes, Retry-After and backoff pauses.

Pure helpers without I/O. A pause is bounded only by the caller's deadline: there is
no attempt count anywhere, and a pause that cannot end before the deadline means the
retry cannot happen.
"""

import email.utils
import math
import random
import ssl
from datetime import datetime, timezone

import requests
from urllib3.exceptions import ConnectTimeoutError, MaxRetryError, NewConnectionError

from yandex_client_modules.response_poller import poll_delay_cap

try:
    from urllib3.util.ssl_match_hostname import CertificateError as _HostnameMismatch
except ImportError:  # an urllib3 layout without its own hostname check
    _HostnameMismatch = ssl.CertificateError

# TLS errors that only the handshake raises: the certificate check runs before any
# byte of the request is sent.
_HANDSHAKE_ERRORS = (ssl.SSLCertVerificationError, _HostnameMismatch)

RETRY_BASE_SECONDS = 0.5
RETRY_FACTOR = 2.0

# The error event code of a rate limit sent before the response id: the stream form of
# HTTP 429, so it is sent again like one.
RATE_LIMIT_EVENT_CODE = "rate_limit_exceeded"

# The window stops growing long before this exponent (it is capped by poll_delay_cap);
# the bound only keeps the float power from overflowing in a very long wait.
_MAX_GROWTH_EXPONENT = 64


def retry_after_seconds(headers):
    """The pause a Retry-After header asks for, in seconds; None when absent or unreadable."""
    if headers is None:
        return None
    try:
        value = headers.get("Retry-After")
        if value is None:
            value = headers.get("retry-after")
    except Exception:
        return None
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        seconds = float(text)
    except ValueError:
        try:
            when = email.utils.parsedate_to_datetime(text)
        except (TypeError, ValueError, IndexError, OverflowError):
            return None
        if when is None:
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        seconds = (when - datetime.now(timezone.utc)).total_seconds()
    if not math.isfinite(seconds):
        return None
    return max(0.0, seconds)


def backoff_delay(attempt, elapsed, remaining, retry_after=None, rng=random.random):
    """Full-jitter pause before retry ``attempt`` (1-based), or None when it cannot end in time.

    The window doubles from RETRY_BASE_SECONDS and never exceeds the poll pause cap of
    response_poller. Retry-After raises the pause to at least the asked value.
    ``remaining`` None means the caller has no deadline.
    """
    attempt = max(1, int(attempt))
    growth = RETRY_BASE_SECONDS * RETRY_FACTOR ** min(attempt - 1, _MAX_GROWTH_EXPONENT)
    window = min(poll_delay_cap(max(0.0, elapsed)), growth)
    delay = rng() * window
    if retry_after is not None:
        delay = max(delay, retry_after)
    if remaining is not None and delay >= remaining:
        return None
    return delay


def _connect_failure(exc):
    """True when requests reports that the connection itself was never made."""
    reason = exc.args[0] if getattr(exc, "args", None) else None
    if isinstance(reason, MaxRetryError):
        return isinstance(reason.reason, (NewConnectionError, ConnectTimeoutError))
    return False


def _linked_errors(exc):
    """The error and every error it wraps: its args, ``reason``, cause and context.

    The context is included because urllib3 raises its wrapper inside the except
    block of the original error.
    """
    seen = set()
    stack = [exc]
    while stack:
        current = stack.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        linked = [*getattr(current, "args", ()), getattr(current, "reason", None)]
        linked.append(current.__cause__)
        linked.append(current.__context__)
        stack.extend(item for item in linked if isinstance(item, BaseException))


def _handshake_failure(exc):
    """True when a TLS error provably comes from the handshake, before the request left.

    urllib3 reports a TLS error met while reading the status line (after the body was
    sent, e.g. a bad record MAC from a mobile middlebox) as the same SSLError, so a
    certificate failure in the wrapped errors proves nothing was sent; _before_send
    proves it by where the error was raised.
    """
    return any(isinstance(error, _HANDSHAKE_ERRORS) for error in _linked_errors(exc))


# The urllib3 pool method that opens (TCP and TLS handshake) a connection before the
# request is written; an error raised inside it means no byte of the request was sent.
_CONNECT_FRAME = "_validate_conn"


def _before_send(exc):
    """True when an error in the chain was raised while the connection was being opened.

    A dropped link or a timeout during the TLS handshake surfaces as the same
    SSLError, ReadTimeout or ConnectionError as one met after the request was sent;
    only the place it was raised tells them apart. Errors from writing the request or
    reading the answer never pass through the connect frame.
    """
    for error in _linked_errors(exc):
        tb = error.__traceback__
        while tb is not None:
            if tb.tb_frame.f_code.co_name == _CONNECT_FRAME:
                return True
            tb = tb.tb_next
    return False


def _sent_nothing(exc):
    """True when a transport error provably happened before the request was sent."""
    if isinstance(exc, requests.exceptions.ConnectionError) and _connect_failure(exc):
        return True
    if isinstance(
        exc,
        (
            requests.exceptions.SSLError,
            requests.exceptions.ReadTimeout,
            requests.exceptions.ConnectionError,
        ),
    ):
        return _handshake_failure(exc) or _before_send(exc)
    return False


def _not_sent(exc):
    """True when a transport error provably happened before the request was sent."""
    if isinstance(
        exc,
        (
            requests.exceptions.ConnectTimeout,
            requests.exceptions.ProxyError,
        ),
    ):
        return True
    return _sent_nothing(exc)


def classify_post(exc, resp, stream_requested):
    """What one POST attempt means for a retry.

    ok: a 2xx (or other non-error) answer. not_sent: the request never reached the
    provider. transient: the provider answered 408, 425, 429 or 503, so the request is
    sent again while retries last; this class does not say the request was refused, the
    quota flag of known_not_accepted does. ambiguous: the provider may have accepted it
    (any other 5xx, including 501, a broken connection, an answer without a status).
    fatal: a refusal that a retry cannot change. A streamed request refused for its
    parameters is fatal too. ``stream_requested`` is kept for the callers' signature and
    does not change the class.
    """
    if exc is not None:
        # A TLS error, timeout or reset is "not sent" only when it provably happened
        # while the connection was opened; after that it may follow a sent request.
        if _not_sent(exc):
            return "not_sent"
        return "ambiguous"
    status = getattr(resp, "status_code", None)
    if not isinstance(status, int):
        return "ambiguous"
    if status < 400:
        return "ok"
    if status in (408, 425, 429, 503):
        return "transient"
    if status >= 500:
        return "ambiguous"
    return "fatal"


def known_not_accepted(exc, resp, error_code=None):
    """The explicit quota flag of a POST whose retries ran out.

    True only when the provider is known not to have accepted the request: it never
    reached the provider (see _not_sent), or the rate limit stayed, which is HTTP 429 or
    the rate_limit_exceeded error event before the response id, its stream form. Every
    other failure after the request was sent may have been accepted, so the reservation
    stays: 408, 425, 503, 501 and other 5xx, timeouts and resets after the send, a stream
    that ended before it named its response, and any other in-stream error event.
    Callers pass this flag on and never decide the release from the outcome class.
    """
    if exc is not None:
        return _not_sent(exc)
    if error_code == RATE_LIMIT_EVENT_CODE:
        return True
    return getattr(resp, "status_code", None) == 429


def classify_poll(exc, resp):
    """What one poll of a stored response means: ok, retry or fatal."""
    if exc is not None:
        return "retry"
    status = getattr(resp, "status_code", None)
    if not isinstance(status, int):
        return "retry"
    if status < 400:
        return "ok"
    if status in (404, 408, 425, 429) or status >= 500:
        return "retry"
    return "fatal"


def failure_reason(exc, resp=None):
    """The reason code of a failed attempt: connect_error, connect_timeout, http_<code>,
    not_found or transport_error."""
    status = getattr(resp, "status_code", None)
    if isinstance(status, int) and status >= 400:
        return "not_found" if status == 404 else f"http_{status}"
    if isinstance(exc, requests.exceptions.ConnectTimeout):
        return "connect_timeout"
    if isinstance(exc, requests.exceptions.ProxyError) or _sent_nothing(exc):
        return "connect_error"
    return "transport_error"


def reason_text(reason):
    """The Russian name of a reason code, as the status line and error texts say it."""
    reason = str(reason or "")
    if reason.startswith("connect_"):
        return "Нет связи с провайдером"
    if reason == "http_429":
        return "Провайдер ограничил частоту запросов (HTTP 429)"
    if reason == "http_503":
        return "Провайдер временно недоступен (HTTP 503)"
    if reason.startswith("http_"):
        code = reason[len("http_") :]
        if code.isdigit() and int(code) >= 500:
            return f"Ошибка на стороне провайдера (HTTP {code})"
        return f"Провайдер не принял запрос (HTTP {code})"
    if reason == "not_found":
        return "Ответ ещё не появился у провайдера"
    if reason == "invalid_json":
        return "Провайдер прислал нечитаемый ответ"
    if reason == "stream_interrupted":
        return "Поток ответа прервался"
    if reason == "provider_error_event":
        return "Провайдер сообщил о временной ошибке"
    return "Связь с провайдером прервалась"


def _lower_first(text):
    return text[:1].lower() + text[1:]


def _seconds(value):
    """Whole seconds for a person: 240.0 reads as 240."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not math.isfinite(number):
        return str(value)
    return f"{max(0, round(number))}"


def exhausted_text(elapsed, reason):
    return (
        f"Провайдер не принял запрос за {_seconds(elapsed)} с "
        f"({_lower_first(reason_text(reason))}). Нажмите «Повторить»."
    )


def retry_after_too_long_text(retry_after, poll_timeout):
    return (
        f"Провайдер просит повторить запрос через {_seconds(math.ceil(retry_after))} с — "
        f"это дольше отведённого ожидания ({_seconds(poll_timeout)} с). "
        "Нажмите «Повторить» позже."
    )


OUTCOME_UNKNOWN = (
    "Связь с провайдером оборвалась сразу после отправки запроса, и неизвестно, принял ли "
    "он его. Повторно платный запрос не отправлялся, чтобы не оплатить ответ дважды. "
    "Нажмите «Повторить», чтобы спросить заново."
)


def server_error_unknown_text(status_code):
    """The person's text for a POST answered with a server error: maybe accepted."""
    return (
        f"Провайдер ответил ошибкой HTTP {status_code} на отправленный запрос, и "
        "неизвестно, принял ли он его. Повторно платный запрос не отправлялся, чтобы "
        "не оплатить ответ дважды. Нажмите «Повторить», чтобы спросить заново."
    )


def poll_timeout_text(timeout):
    return (
        f"Провайдер не прислал готовый ответ за {_seconds(timeout)} с. "
        "Нажмите «Повторить», чтобы спросить ещё раз."
    )


def conversation_failed_text(elapsed, reason):
    return (
        f"Провайдер не создал диалог за {_seconds(elapsed)} с "
        f"({_lower_first(reason_text(reason))}). Нажмите «Повторить»."
    )
