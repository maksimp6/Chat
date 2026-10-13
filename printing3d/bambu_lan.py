"""Opt-in read-only Bambu A1 mini LAN MQTT monitor.

No printer actions are published. The caller supplies a trusted serial and
access code from a local secret provider, never from repository configuration.
"""

from __future__ import annotations

import importlib
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
    if not isinstance(decoded, dict):
        raise ValueError("invalid printer report")
    return record_bambu_print_report(owner_id, printer_id, decoded, timestamp)


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

    def on_message(_client: Any, _userdata: Any, message: Any) -> None:
        try:
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
    """Wait for one unsolicited report. Never publish commands or credentials.

    Bambu LAN firmware uses a device-local self-signed TLS certificate.
    The connection is LAN-scoped, but certificate identity cannot be verified
    without a separately pinned trust anchor.
    """
    if not access_code or not host or timeout <= 0 or timeout > 120:
        raise ValueError("missing printer credentials or invalid timeout")
    topic = printer_report_topic(serial)
    mqtt = importlib.import_module("paho.mqtt.client")
    received: list[dict[str, Any]] = []
    failures: list[str] = []
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, protocol=mqtt.MQTTv311)
    client.username_pw_set("bblp", access_code)
    if not trusted_cert_pem or "BEGIN CERTIFICATE" not in trusted_cert_pem:
        raise ValueError("trusted printer certificate required")
    context = ssl.create_default_context()
    context.check_hostname = False  # Fixed LAN IP; identity bound to configured cert.
    context.load_verify_locations(cadata=trusted_cert_pem)
    client.tls_set_context(context)

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
