import json
from unittest.mock import patch

import pytest
import requests

from cloud.base import CloudProviderError
from cloud.cloudru.client import CloudRuClient
from trace_manager import ExecutionTrace


def test_cloudru_network_error_never_captures_authorization_in_trace(monkeypatch):
    monkeypatch.setenv("CLOUDRU_API_KEY", "secret-token")
    monkeypatch.setenv("CLOUDRU_COMPUTE_ENDPOINT", "https://compute.example")
    trace = ExecutionTrace(trace_id="cloudru-secret-test")

    with (
        patch("cloud.cloudru.client.get_current_trace", return_value=trace),
        patch(
            "cloud.cloudru.client.requests.request",
            side_effect=requests.RequestException("offline"),
        ),
        pytest.raises(CloudProviderError),
    ):
        CloudRuClient().request("compute", "GET", "/v1/instances")

    snapshot = json.dumps(trace.make_snapshot(), ensure_ascii=False)
    assert "secret-token" not in snapshot
    assert "Authorization" not in snapshot
    assert trace.trace["errors"][0]["source"] == "cloudru.request"
