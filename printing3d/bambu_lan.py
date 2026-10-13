"""Opt-in read-only Bambu A1 mini LAN MQTT monitor.

No printer actions are published. The caller supplies a trusted serial and
access code from a local secret provider, never from repository configuration.
"""

from __future__ import annotations

import importlib
import hashlib
import hmac
import json
import ssl
import time
from typing import Any, Callable

from .service import record_bambu_print_report


def printer_report_topic(serial: str) -> str:
    """Require a concrete device ID: wildcard topics are never subscribed."""
    if not serial or len(serial) > 64 or not serial.isascii() or not serial.isalnum():
        raise ValueError("invalid printer serial")
    return f"device/{serial}/report"


def consume_print_report(
    owner_id: str,
    printer_id: str,
    topic: str,
    payload: bytes,
    timestamp: float,
) -> bool:
    """Persist an authenticated status report, rejecting unrelated MQTT topics."""
    if topic != printer_report_topic(printer_id):
        raise ValueError("unexpected printer topic")
    if len(payload) > 256 * 1024:
        raise ValueError("oversized printer message")
    decoded = json.loads(payload)
    if not isinstance(decoded, dict) or not isinstance(decoded.get("print"), dict):
        raise ValueError("invalid printer report")
    if "gcode_state" not in decoded["print"]:
        raise ValueError("not a printer state report")
    return record_bambu_print_report(owner_id, printer_id, decoded, timestamp)


def _certificate_fingerprint(pem: str) -> bytes:
    """Require a configured PEM; do not accept arbitrary network certificates."""
    if not pem or "BEGIN CERTIFICATE" not in pem:
        raise ValueError("trusted printer certificate required")
    try:
        der = ssl.PEM_cert_to_DER_cert(pem)
    except ValueError as exc:
        raise ValueError("invalid printer certificate") from exc
    return hashlib.sha256(der).digest()


def _install_tls_pin(client: Any, expected_fingerprint: bytes) -> None:
    """Check the handshake peer before Paho transmits its MQTT CONNECT credentials.

    Paho reconnect invokes on_socket_open *before* _send_connect.  All reconnects
    use the same callback; mismatch raises and prevents MQTT credential delivery.
    """

    def on_socket_open(_client: Any, _userdata: Any, sock: Any) -> None:
        if not isinstance(sock, ssl.SSLSocket):
            raise ssl.SSLError("printer MQTT socket is not TLS")
        cert = sock.getpeercert(binary_form=True)
        if not cert or not hmac.compare_digest(hashlib.sha256(cert).digest(), expected_fingerprint):
            raise ssl.SSLError("printer TLS peer differs from trusted certificate")

    client.suppress_exceptions = False
    client.on_socket_open = on_socket_open


def _install_callbacks(
    client: Any,
    owner_id: str,
    serial: str,
    topic: str,
    clock: Callable[[], float],
    received: list[dict[str, Any]],
    failures: list[str],
) -> None:
    def on_connect(client: Any, _userdata: Any, _flags: Any, reason: Any, _props: Any) -> None:
        if reason.is_failure:
            failures.append("MQTT authentication failed")
        else:
            client.subscribe(topic, qos=0)

    def on_subscribe(_client: Any, _userdata: Any, _mid: Any, reasons: Any, _props: Any) -> None:
        if any(reason.is_failure for reason in reasons):
            failures.append("MQTT subscription rejected")
        else:
            from .bambu_requests import encode_pushall, request_topic

            _client.publish(request_topic(serial), encode_pushall("alice_status_1"), qos=0)

    def on_message(_client: Any, _userdata: Any, message: Any) -> None:
        try:
            decoded = json.loads(message.payload)
            if not isinstance(decoded, dict) or not isinstance(decoded.get("print"), dict):
                return
            if "gcode_state" not in decoded["print"]:
                return  # Partial reports/ACKs are not evidence of state changes.
            changed = consume_print_report(
                owner_id, serial, message.topic, message.payload, clock()
            )
            received.append({"received": True, "changed": changed, "at": clock()})
        except (ValueError, UnicodeError, json.JSONDecodeError):
            failures.append("invalid MQTT report")

    client.on_connect = on_connect
    client.on_subscribe = on_subscribe
    client.on_message = on_message


def read_live_status(
    owner_id: str,
    serial: str,
    access_code: str,
    *,
    host: str,
    timeout: float = 20,
    clock: Callable[[], float] = time.time,
    trusted_cert_pem: str,
) -> dict[str, Any]:
    """Wait for a status report using one read-only pushall query.

    Bambu LAN firmware uses a device-local self-signed TLS certificate.
    The connection is LAN-scoped. Its exact DER certificate is verified via a
    pre-CONNECT TLS socket hook, because the device leaf may lack X.509 CA KU.
    """
    if not access_code or not host or timeout <= 0 or timeout > 120:
        raise ValueError("missing printer credentials or invalid timeout")
    expected_fingerprint = _certificate_fingerprint(trusted_cert_pem)
    topic = printer_report_topic(serial)
    received: list[dict[str, Any]] = []
    failures: list[str] = []
    mqtt = importlib.import_module("paho.mqtt.client")
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, protocol=mqtt.MQTTv311)
    # Bambu's device leaf lacks CA key-usage extensions. The peer is pinned in
    # on_socket_open *before* Paho transmits MQTT CONNECT/password.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    client.tls_set_context(context)
    _install_tls_pin(client, expected_fingerprint)
    client.username_pw_set("bblp", access_code)
    _install_callbacks(client, owner_id, serial, topic, clock, received, failures)
    client.connect(host, port=8883, keepalive=15)
    client.loop_start()
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not received and not failures:
            time.sleep(0.1)
        if failures:
            return {"received": False, "error": failures[0]}
        if received:
            return received[0]
        return {"received": False, "error": "No status report received before timeout"}
    finally:
        client.loop_stop()
        client.disconnect()
