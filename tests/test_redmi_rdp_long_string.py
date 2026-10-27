"""The real browser transport must consume Firefox's long-string protocol."""

import json

import pytest

from scripts.redmi_screenshot_capture import RDP, capture_url


def test_evaluation_reads_complete_unicode_json_in_protocol_chunks():
    text = json.dumps({"question": "Логос " * 13000 + "🧪", "tool_calls": []}, ensure_ascii=False)
    encoded = text.encode("utf-16-le")
    length = len(encoded) // 2
    rdp = object.__new__(RDP)
    requests = []
    responses = [{"type": "evaluationResult", "result": {
        "type": "longString", "actor": "test-long-string", "initial": text[:1000], "length": length,
    }}]

    def send(packet):
        requests.append(packet)
        if packet["type"] == "substring":
            responses.append({"from": packet["to"], "substring": encoded[
                packet["start"] * 2:packet["end"] * 2
            ].decode("utf-16-le", "surrogatepass")})

    rdp.send = send
    rdp.receive = lambda: responses.pop(0)
    assert rdp.evaluate("console", "JSON.stringify(value)") == json.loads(text)
    reads = [item for item in requests if item["type"] == "substring"]
    assert len(reads) > 1
    assert reads[0]["start"] == 0 and reads[-1]["end"] == length


def test_partial_long_string_is_rejected_instead_of_truncated():
    rdp = object.__new__(RDP)
    rdp.send = lambda _: None
    rdp.receive = lambda: {"from": "test-long-string", "substring": "short"}
    with pytest.raises(RuntimeError, match="Incomplete DevTools long string"):
        rdp.read_long_string({"actor": "test-long-string", "length": 10000})


def test_candidate_url_cannot_target_another_device():
    assert capture_url("http://127.0.0.1:8081/") == "http://127.0.0.1:8081/"
    with pytest.raises(ValueError):
        capture_url("http://192.168.0.4:8080/")
