"""Read-only Cloud.ru check: allow-listed output, per-check isolation, no secret values."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from cloud.base import CloudProviderError
from scripts import cloudru_check as check

SECRET_VALUE = "super-secret-value-123"


def container(**overrides):
    value = {
        "name": "chrome-test-22706bfa6066",
        "id": "1440c7da-b147-4ed1-87fb-f0bdf2ec5b77",
        "status": "RUNNING",
        "description": "x",
        "configuration": {
            "ingress": {"publiclyAccessible": True, "publicUri": "chrome.containerapps.ru"}
        },
        "template": {
            "scaling": {"minInstanceCount": 0, "maxInstanceCount": 1},
            "containers": [
                {
                    "image": "alice-chrome-browser.cr.cloud.ru/chrome-worker@sha256:" + "b" * 64,
                    "resources": {"cpu": "0.5", "memory": "1024Mi"},
                    "env": [{"name": "BROWSER_API_TOKEN", "value": SECRET_VALUE}],
                }
            ],
        },
    }
    value.update(overrides)
    return value


def test_containers_report_only_allow_listed_fields_and_never_env_values():
    apps = SimpleNamespace(list=Mock(return_value=[container()]))
    result = check.containers(apps)
    assert result == [
        {
            "name": "chrome-test-22706bfa6066",
            "status": "RUNNING",
            "cpu": "0.5",
            "memory": "1024Mi",
            "min_instances": 0,
            "max_instances": 1,
            "public": True,
            "digest": "sha256:" + "b" * 64,
            "env_names": ["BROWSER_API_TOKEN"],
        }
    ]
    assert SECRET_VALUE not in json.dumps(result)


def test_malformed_container_records_degrade_to_nulls_instead_of_crashing():
    result = check.containers(SimpleNamespace(list=Mock(return_value=[{"name": "odd"}, "garbage"])))
    assert result[0]["name"] == "odd"
    assert result[0]["status"] is None and result[0]["digest"] is None
    assert result[1] == {"name": None}


def test_registries_list_names_and_statuses_only(monkeypatch):
    monkeypatch.setattr(
        check,
        "registry_inventory",
        lambda registry: [
            {
                "id": "x",
                "name": "alice-chrome-browser",
                "status": "ACTIVE",
                "isPublic": False,
                "secret": SECRET_VALUE,
            }
        ],
    )
    result = check.registries(SimpleNamespace())
    assert result == [{"name": "alice-chrome-browser", "status": "ACTIVE", "public": False}]


def test_secret_check_returns_version_metadata_and_never_payloads():
    client = SimpleNamespace(
        list_versions=Mock(
            return_value=[
                {
                    "id": "v1",
                    "status": "ACTIVE",
                    "created_at": "t",
                    "payload": SECRET_VALUE,
                    "value": SECRET_VALUE,
                }
            ]
        ),
        get_secret_value=Mock(side_effect=AssertionError("payload must never be read")),
    )
    result = check.secret_versions(client, "my-secret")
    assert result == {
        "secret_id_set": True,
        "versions": [{"id": "v1", "status": "ACTIVE", "created_at": "t"}],
    }
    assert SECRET_VALUE not in json.dumps(result)
    client.get_secret_value.assert_not_called()


def test_secret_check_needs_an_identifier_and_viewer_credentials(monkeypatch):
    for key in ("CLOUDRU_SECRET_MANAGEMENT_KEY_ID", "CLOUDRU_SECRET_MANAGEMENT_KEY_SECRET"):
        monkeypatch.delenv(key, raising=False)
    skipped = check.run(["secrets"], env={}, factories={})
    assert skipped["secrets"] == {"ok": False, "skipped": "secret_id_not_provided"}
    with_id = check.run(["secrets"], env={"CHECK_SECRET_ID": "abc"}, factories={})
    assert with_id["secrets"] == {
        "ok": False,
        "skipped": "secret_management_credentials_not_configured",
    }


def test_a_failing_check_does_not_hide_the_others_and_errors_are_allow_listed():
    def broken():
        raise CloudProviderError(
            "Authorization: Bearer leaked-token", code="auth_failed", http_status=401
        )

    factories = {
        "auth": lambda env: SimpleNamespace(_token=broken),
        "containers": lambda env: SimpleNamespace(list=Mock(return_value=[container()])),
    }
    report = check.run(["auth", "containers"], env={}, factories=factories)
    assert report["auth"] == {"ok": False, "error": "auth_failed", "http_status": 401}
    assert report["containers"]["ok"] is True
    assert "leaked-token" not in json.dumps(report)
    odd = check.error_result(CloudProviderError("token abc", code="token abc", http_status=200))
    assert odd == {"ok": False, "error": "check_failed", "http_status": 200}


def test_unknown_checks_are_rejected_and_all_expands_to_every_check():
    with pytest.raises(ValueError):
        check.run(["delete_everything"], env={}, factories={})
    assert check.expand("all") == ["auth", "containers", "registries", "secrets"]
    assert check.expand("containers") == ["containers"]


def test_summary_markdown_lists_results_without_values():
    report = {
        "auth": {"ok": True},
        "containers": {
            "ok": True,
            "data": check.containers(SimpleNamespace(list=Mock(return_value=[container()]))),
        },
    }
    text = check.markdown(report)
    assert "chrome-test-22706bfa6066" in text and "RUNNING" in text
    assert SECRET_VALUE not in text
