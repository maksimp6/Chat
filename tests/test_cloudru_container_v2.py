"""HTTP regressions for the public v2 schema and the observed missing-name 499."""

import copy
import json
from unittest.mock import Mock

import pytest
import requests

from cloud.base import CloudProviderError
from cloud.cloudru.client import CloudRuClient
from cloud.cloudru.container_apps_client import CloudRuContainerAppsClient, ContainerSpec


def _client(monkeypatch, replies):
    calls = []
    pending = list(replies)

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        status, payload = pending.pop(0)
        response = requests.Response()
        response.status_code = status
        response._content = json.dumps(payload).encode()
        return response

    monkeypatch.setattr("cloud.cloudru.client.requests.request", request)
    monkeypatch.delenv("CLOUDRU_CONTAINER_APPS_ENDPOINT", raising=False)
    iam = Mock()
    iam._token.return_value = "test-token"
    apps = CloudRuContainerAppsClient(
        project_id="project-test", client=CloudRuClient(iam_client=iam, api_key_auth=False)
    )
    return apps, calls


def test_get_uses_v2_with_project_query(monkeypatch):
    apps, calls = _client(monkeypatch, [(200, {"name": "alice-pro", "status": "running"})])
    assert apps.status("alice-pro")["exists"] is True
    assert calls[0][:2] == ("GET", "https://containers.api.cloud.ru/v2/containers/alice-pro")
    assert calls[0][2]["params"] == {"projectId": "project-test"}


def test_499_missing_name_requires_successful_complete_inventory(monkeypatch):
    apps, calls = _client(
        monkeypatch,
        [
            (499, {}),
            (200, {"data": [{"name": "other"}], "nextPageToken": "page-2"}),
            (200, {"data": [], "nextPageToken": ""}),
        ],
    )
    assert apps.status("alice-pro") == {"name": "alice-pro", "exists": False, "status": "NOT_FOUND"}
    assert all(call[0] == "GET" for call in calls)
    assert calls[1][1] == calls[2][1] == "https://containers.api.cloud.ru/v2/containers"
    assert calls[1][2]["params"] == {"projectId": "project-test", "pageSize": 100}
    assert calls[2][2]["params"] == {
        "projectId": "project-test",
        "pageSize": 100,
        "pageToken": "page-2",
    }


def test_499_existing_name_on_later_page_must_not_create(monkeypatch):
    apps, calls = _client(
        monkeypatch,
        [
            (499, {}),
            (200, {"data": [{"name": "other"}], "nextPageToken": "page-2"}),
            (200, {"data": [{"name": "alice-pro"}]}),
        ],
    )
    with pytest.raises(CloudProviderError) as error:
        apps.deploy(ContainerSpec(name="alice-pro", image="new-image"))
    assert error.value.http_status == 499
    assert len(calls) == 3
    assert all(call[0] == "GET" for call in calls)


@pytest.mark.parametrize(
    "inventory",
    [
        [(403, {})],
        [(200, {})],
        [(200, {"data": [], "total": 1})],
        [(200, {"data": [], "total": "invalid"})],
        [(200, {"data": [], "nextPageToken": 0})],
        [(200, {"data": [{"id": "missing-name"}]})],
        [(200, {"data": [], "nextPageToken": "same"})] * 2,
    ],
)
def test_499_failed_or_invalid_inventory_must_not_create(monkeypatch, inventory):
    apps, calls = _client(monkeypatch, [(499, {}), *inventory])
    with pytest.raises(CloudProviderError):
        apps.deploy(ContainerSpec(name="alice-pro", image="new-image"))
    assert all(call[0] == "GET" for call in calls)


@pytest.mark.parametrize("operation", ["update", "restore"])
def test_patch_projects_response_onto_writable_schema(monkeypatch, operation):
    previous = {
        "name": "alice-pro",
        "projectId": "response-project",
        "id": "response-id",
        "status": "running",
        "createdBy": "creator",
        "metadata": {"response": True},
        "configuration": {
            "privileged": False,
            "autoDeployments": {"enabled": False},
            "ssh": {"enabled": False},
            "ingress": {
                "publiclyAccessible": True,
                "publicUri": "public.example",
                "internalUri": "internal.example",
                "additionalPortMappings": [{"port": 8081, "url": "response.example"}],
            },
        },
        "template": {
            "timeout": "300s",
            "idleTimeout": "600s",
            "protocol": "http_1",
            "containers": [{"name": "alice-pro", "image": "old", "env": []}],
            "scaling": {"minInstanceCount": 0, "maxInstanceCount": 1},
            "volumes": [],
            "initContainers": [],
            "responseOnly": "remove",
        },
    }
    unchanged = copy.deepcopy(previous)
    replies = [(200, previous), (200, {})] if operation == "update" else [(200, {})]
    apps, calls = _client(monkeypatch, replies)
    if operation == "update":
        apps.update(ContainerSpec(name="alice-pro", image="new"))
    else:
        apps.restore("alice-pro", previous)
    method, url, request = calls[-1]
    assert (method, url) == ("PATCH", "https://containers.api.cloud.ru/v2/containers/alice-pro")
    assert request["params"] is None
    body = request["json"]
    assert set(body) == {"name", "projectId", "configuration", "template"}
    assert body["projectId"] == "project-test"
    assert set(body["configuration"]) == {"autoDeployments", "ssh", "ingress"}
    assert body["configuration"]["ingress"] == {
        "publiclyAccessible": True,
        "additionalPortMappings": [{"port": 8081}],
    }
    assert "responseOnly" not in body["template"]
    assert body["template"]["containers"][0]["image"] == ("new" if operation == "update" else "old")
    assert body["template"]["volumes"] == []
    assert body["template"]["initContainers"] == []
    assert previous == unchanged
