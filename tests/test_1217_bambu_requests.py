"""Regression contracts for outbound Bambu MQTT and safe capture."""

import json

import pytest

from printing3d.bambu_requests import (
    MockBroker,
    capture_request,
    encode_pushall,
    request_topic,
)


def test_encode_known_wire_contract_and_mock_broker():
    serial = "A1MINI123"
    topic = request_topic(serial)
    payload = encode_pushall("alice_status_1")
    assert json.loads(payload) == {
        "pushing": {
            "sequence_id": "alice_status_1",
            "command": "pushall",
            "version": 1,
            "push_target": 1,
        }
    }
    broker = MockBroker(serial)
    assert broker.publish(topic, payload)["command"] == "pushall"
    assert broker.requested == 1


@pytest.mark.parametrize("bad", ["", "!", "x" * 65, "key\nsecret", "привет"])
def test_sequence_id_rejected(bad):
    with pytest.raises(ValueError):
        encode_pushall(bad)


@pytest.mark.parametrize("bad", ["+", "ABC/#", "", "../x"])
def test_topic_rejected(bad):
    with pytest.raises(ValueError):
        request_topic(bad)


@pytest.mark.parametrize(
    "body",
    [
        {"pushing": {"command": "stop", "sequence_id": "test", "version": 1, "push_target": 1}},
        {"system": {"command": "reboot"}},
        {
            "pushing": {
                "command": "pushall",
                "sequence_id": "x",
                "version": 1,
                "push_target": 1,
                "access_code": "do-not-leak",
            }
        },
        [1, 2],
    ],
)
def test_dangerous_or_unknown_request_cannot_be_replayed(body):
    broker = MockBroker("ABC")
    with pytest.raises(ValueError):
        broker.publish(request_topic("ABC"), json.dumps(body).encode())
    assert broker.requested == 0


def test_capture_does_not_leak_serial_or_sequence_or_secret():
    payload = encode_pushall("private_sequence_id")
    event = capture_request(request_topic("SERIALSECRET"), payload, "SERIALSECRET")
    rendered = json.dumps(event)
    assert "private_sequence_id" not in rendered
    assert "SERIALSECRET" not in rendered
    assert set(event) == {"family", "command", "version", "push_target", "payload_bytes"}


def test_reject_other_printer_and_oversize():
    with pytest.raises(ValueError):
        capture_request(request_topic("OTHER"), encode_pushall("a"), "ABC")
    with pytest.raises(ValueError):
        capture_request(request_topic("ABC"), b"x" * 4097, "ABC")
