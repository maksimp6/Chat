"""Permanent RDC admission, ownership, private storage, and lifecycle contracts."""

import copy
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from cloud.base import CloudProviderError
from scripts import cloudru_rdc as rdc
from storage import StorageObjectNotFound

PROJECT = "94ae3a86-671f-40ae-9323-e81d3626135e"
IDENTIFIER = "1440c7da-b147-4ed1-87fb-f0bdf2ec5b77"
IMAGE = "alice-rdc-probe.cr.cloud.ru/chromium-probe@sha256:" + "a" * 64
DEVICE = "5c1a65e6-dce5-43ea-af34-82e7c9345ed5"


def record():
    body = rdc.creation_body(PROJECT, IMAGE)
    body.update(id=IDENTIFIER, status="running")
    body["configuration"]["ingress"]["publicUri"] = "https://rdc-test.containerapps.ru"
    return body


def health(**overrides):
    return {
        "mode": "cloud-rdc",
        "browser_ready": True,
        "rdc_running": True,
        "state_ready": True,
        "paired": True,
        "device_id": DEVICE,
        "checkpoint_generation": 1,
        **overrides,
    }


def apps_with(item=None):
    apps = SimpleNamespace(
        project_id=PROJECT,
        client=Mock(timeout=20),
        list=Mock(return_value=[item or record()]),
        stop=Mock(),
        start=Mock(),
    )
    return apps


def test_permanent_app_requires_managed_private_state_and_native_authorization():
    body = rdc.creation_body(PROJECT, IMAGE)
    assert body["template"]["scaling"] == {"minInstanceCount": 1, "maxInstanceCount": 1}
    assert body["configuration"]["ingress"]["accessSettings"] == {"enableAuth": True}
    serialized = json.dumps(body)
    for forbidden in ("CLOUDRU_IAM_KEY", "SSH", '"PORT"', "privileged", "no-sandbox"):
        assert forbidden not in serialized
    assert body["template"]["volumes"][0]["volumeAttributes"]["bucketName"] == rdc.names(PROJECT)[1]


@pytest.mark.parametrize(
    "change",
    [
        lambda p: p.update(projectId=DEVICE),
        lambda p: p.update(description="someone else's app"),
        lambda p: p["configuration"]["ingress"]["accessSettings"].update(enableAuth=False),
        lambda p: p["configuration"].update(privileged=True),
        lambda p: p["configuration"]["autoDeployments"].update(enabled=True),
        lambda p: p["template"]["scaling"].update(maxInstanceCount=2),
        lambda p: p["template"]["volumes"][0]["volumeAttributes"].update(bucketName="unrelated"),
        lambda p: p["template"]["volumes"][0]["volumeAttributes"].update(
            entrypoint="https://evil.example"
        ),
        lambda p: p["template"]["containers"][0]["volumeMounts"][0].update(readOnly=True),
        lambda p: p["template"]["containers"][0]["env"].append(
            {"name": "ALICE_RDC_MODE", "value": "cloud-rdc"}
        ),
    ],
)
def test_ownership_blocks_unrelated_or_weakened_config(change):
    item = record()
    change(item)
    apps = apps_with(item)
    with pytest.raises(CloudProviderError):
        rdc.owned_record(apps)
    apps.stop.assert_not_called()


def test_proto_defaults_and_expanded_managed_volume_do_not_break_ownership():
    item = record()
    item["template"]["containers"][0]["volumeMounts"][0].pop("readOnly")
    item["template"]["volumes"][0]["volumeAttributes"].update(
        entrypoint="https://s3.cloud.ru", region="ru-central-1", tenantId=DEVICE
    )
    assert rdc.owned_record(apps_with(item), identifier=IDENTIFIER, image=IMAGE)["id"] == IDENTIFIER
    with pytest.raises(CloudProviderError):
        rdc.owned_record(apps_with(item), identifier=DEVICE)


def test_ambiguous_inventory_cannot_authorize_mutation():
    apps = apps_with()
    apps.list.return_value = [record(), record()]
    with pytest.raises(CloudProviderError):
        rdc.owned_record(apps)


@pytest.mark.parametrize(
    "uri",
    [
        "https://containerapps.ru",
        "https://rdc.containerapps.ru.evil.example",
        "http://rdc.containerapps.ru",
        "https://u:p@rdc.containerapps.ru",
        "https://rdc.containerapps.ru:8443",
        "https://rdc.containerapps.ru/file",
    ],
)
def test_only_provider_https_origin_is_contacted(uri):
    item = record()
    item["configuration"]["ingress"]["publicUri"] = uri
    with pytest.raises(CloudProviderError):
        rdc.application_origin(item)


def test_anonymous_health_gate_does_not_request_pairing_or_follow_redirects():
    get = Mock(
        return_value=SimpleNamespace(
            status_code=302, headers={"Location": "https://console.cloud.ru/login"}
        )
    )
    rdc.verify_anonymous_gate(record(), http_get=get)
    get.assert_called_once_with(
        "https://rdc-test.containerapps.ru/healthz", timeout=5, allow_redirects=False
    )
    get.return_value = SimpleNamespace(status_code=200, headers={})
    with pytest.raises(CloudProviderError, match="could not be verified"):
        rdc.verify_anonymous_gate(record(), http_get=get)


def test_iam_call_is_fixed_path_bounded_and_restores_checkpoint_timeout():
    apps = apps_with()
    apps.client.request.return_value = {"statusCode": 200, "body": json.dumps(health())}
    assert rdc.test_call(apps, "/healthz") == health()
    _, kwargs = apps.client.request.call_args
    assert kwargs["json_body"] == {
        "name": rdc.names(PROJECT)[0],
        "projectId": PROJECT,
        "method": "GET",
        "path": "/healthz",
    }
    with pytest.raises(CloudProviderError):
        rdc.test_call(apps, "/rdc/pair")
    apps.client.request.side_effect = RuntimeError("not logged")
    with pytest.raises(RuntimeError):
        rdc.test_call(apps, "/checkpoint", "POST")
    assert apps.client.timeout == 20


@pytest.mark.parametrize(
    "payload",
    [
        {"statusCode": True, "body": "{}"},
        {"statusCode": 200, "body": "x" * 8193},
        {"statusCode": 200, "body": "{}", "isBase64Encoded": True},
        {"statusCode": 200, "body": "[]"},
        {"statusCode": 200, "body": "garbage"},
    ],
)
def test_unknown_testcall_response_is_not_runtime_success(payload):
    apps = apps_with()
    apps.client.request.return_value = payload
    with pytest.raises(CloudProviderError):
        rdc.test_call(apps, "/healthz")


@pytest.mark.parametrize(
    "value",
    [
        health(access_token="must not leak"),
        health(device_id=None),
        health(paired="true"),
        health(checkpoint_generation=True),
    ],
)
def test_health_never_echoes_unknown_auth_fields(value):
    with pytest.raises(CloudProviderError):
        rdc.health_summary(value)


def acl(grantee="creator", kind="CanonicalUser"):
    return (
        f'<AccessControlPolicy xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f"<Owner><ID>creator</ID></Owner><AccessControlList><Grant>"
        f'<Grantee xsi:type="{kind}"><ID>{grantee}</ID></Grantee><Permission>FULL_CONTROL</Permission>'
        "</Grant></AccessControlList></AccessControlPolicy>"
    ).encode()


@pytest.mark.parametrize(
    "content",
    [
        acl("public", "Group"),
        acl("other-user"),
        b"<broken>",
        b"<!DOCTYPE foo><AccessControlPolicy/>",
    ],
)
def test_non_private_or_unknown_acl_prevents_bucket_use(content):
    store = Mock()
    store._request.return_value.content = content
    with pytest.raises(CloudProviderError):
        rdc.verify_private_acl(store, object())


def test_bucket_requires_private_acl_and_exact_owner_marker():
    store = Mock()
    store._request.return_value.content = acl()
    store.download.return_value = json.dumps(rdc.owner_marker(PROJECT)).encode()
    assert rdc.bucket_inventory(store, object(), PROJECT)
    store.download.return_value = b'{"purpose":"other"}'
    with pytest.raises(CloudProviderError):
        rdc.bucket_inventory(store, object(), PROJECT)
    store.upload.assert_not_called()


def test_bucket_creation_is_single_attempt_and_verified_before_marker(monkeypatch):
    store = Mock()
    checks = iter([False, True])
    monkeypatch.setattr(rdc, "bucket_inventory", lambda *a: next(checks))
    verify = Mock()
    monkeypatch.setattr(rdc, "verify_private_acl", verify)
    credentials = object()
    rdc.prepare_bucket(store, credentials, PROJECT)
    store._request.assert_called_once_with("PUT", credentials=credentials)
    verify.assert_called_once_with(store, credentials)
    assert store.upload.call_args.args[0] == "owner.json"


def test_missing_bucket_is_read_only_and_tenant_is_never_project_fallback():
    store = Mock()
    store._request.side_effect = StorageObjectNotFound("missing")
    assert rdc.bucket_inventory(store, object(), PROJECT) is False
    store.upload.assert_not_called()
    with pytest.raises(CloudProviderError):
        rdc.storage_client(
            PROJECT, "", env={"CLOUDRU_IAM_KEY_ID": "key", "CLOUDRU_IAM_KEY_SECRET": "secret"}
        )


def test_install_never_takes_over_an_existing_named_app(monkeypatch):
    apps = apps_with()
    prepare = Mock()
    monkeypatch.setattr(rdc, "prepare_bucket", prepare)
    with pytest.raises(CloudProviderError) as error:
        rdc.install(apps, Mock(), object(), IMAGE)
    assert error.value.code == "already_exists"
    prepare.assert_not_called()
    apps.client.request.assert_not_called()


def test_failed_install_stops_exact_attempt_without_deleting_state(monkeypatch, capsys):
    apps = apps_with()
    monkeypatch.setattr(rdc, "named_record", lambda apps: None)
    monkeypatch.setattr(rdc, "prepare_bucket", Mock())
    apps.client.request.return_value = {"resourceId": IDENTIFIER}
    monkeypatch.setattr(
        rdc,
        "wait_ready",
        Mock(side_effect=CloudProviderError("secret-message", code="invalid_response")),
    )
    stop = Mock(return_value=record())
    monkeypatch.setattr(rdc, "stop_owned", stop)
    with pytest.raises(CloudProviderError):
        rdc.install(apps, Mock(), object(), IMAGE)
    stop.assert_called_once_with(apps, identifier=IDENTIFIER, image=IMAGE)
    assert "secret-message" not in capsys.readouterr().out


def test_restart_requires_checkpoint_and_confirmed_suspension_before_start(monkeypatch):
    apps = apps_with()
    events = []
    monkeypatch.setattr(rdc, "checkpoint", lambda a: (record(), health(), 2))
    monkeypatch.setattr(rdc, "stop_owned", lambda *a, **kw: events.append("suspended") or record())
    apps.start.side_effect = lambda *a: events.append("start")
    monkeypatch.setattr(
        rdc, "wait_ready", lambda *a, **kw: (record(), health(checkpoint_generation=2))
    )
    monkeypatch.setattr(rdc, "verify_anonymous_gate", Mock())
    result = rdc.restart(apps)
    assert events == ["suspended", "start"]
    assert result["restart_verified"] and result["device_id"] == DEVICE


def test_restart_rejects_stale_or_changed_authorization_and_suspends(monkeypatch):
    apps = apps_with()
    monkeypatch.setattr(rdc, "checkpoint", lambda a: (record(), health(), 2))
    stop = Mock(return_value=record())
    monkeypatch.setattr(rdc, "stop_owned", stop)
    monkeypatch.setattr(
        rdc,
        "wait_ready",
        lambda *a, **kw: (record(), health(device_id=PROJECT, checkpoint_generation=2)),
    )
    with pytest.raises(CloudProviderError) as error:
        rdc.restart(apps)
    assert error.value.code == "rdc_restore_unconfirmed"
    assert stop.call_count == 2


def test_ambiguous_stop_never_starts_another_writer(monkeypatch):
    apps = apps_with()
    monkeypatch.setattr(rdc, "checkpoint", lambda a: (record(), health(), 2))
    monkeypatch.setattr(
        rdc,
        "stop_owned",
        Mock(side_effect=CloudProviderError("unknown", code="rdc_cleanup_unconfirmed")),
    )
    with pytest.raises(CloudProviderError):
        rdc.restart(apps)
    apps.start.assert_not_called()


def test_checkpoint_success_requires_new_monotonic_generation(monkeypatch):
    apps = apps_with()
    monkeypatch.setattr(rdc, "wait_ready", lambda a: (record(), health()))
    monkeypatch.setattr(
        rdc, "test_call", lambda *a: {"status": "CHECKPOINT_COMPLETE", "generation": 1}
    )
    with pytest.raises(CloudProviderError):
        rdc.checkpoint(apps)
    apps.stop.assert_not_called()


def test_error_output_never_contains_provider_message_or_auth():
    exc = CloudProviderError("access_token=secret", code="rdc_bucket_not_private")
    assert rdc.safe_error(exc) == {"error": "rdc_bucket_not_private", "http_status": None}
    request_error = requests.RequestException("arbitrary provider response")
    assert "arbitrary" not in json.dumps(rdc.safe_error(request_error))


def test_resume_requires_confirmed_suspended_owner_then_verifies_restored_state(monkeypatch):
    item = record()
    item["status"] = "suspended"
    apps = apps_with(item)
    monkeypatch.setattr(rdc, "wait_ready", lambda *a, **kw: (record(), health()))
    monkeypatch.setattr(rdc, "verify_anonymous_gate", Mock())
    result = rdc.resume(apps)
    apps.start.assert_called_once_with(item["name"])
    assert result["device_id"] == DEVICE
    apps.list.return_value = [record()]
    with pytest.raises(CloudProviderError) as error:
        rdc.resume(apps)
    assert error.value.code == "rdc_not_suspended"


def test_failed_resume_is_suspended_before_another_start(monkeypatch):
    item = record()
    item["status"] = "suspended"
    apps = apps_with(item)
    monkeypatch.setattr(
        rdc, "wait_ready", Mock(side_effect=CloudProviderError("failed", code="rdc_runtime_failed"))
    )
    stop = Mock(return_value=item)
    monkeypatch.setattr(rdc, "stop_owned", stop)
    with pytest.raises(CloudProviderError):
        rdc.resume(apps)
    stop.assert_called_once_with(apps, identifier=IDENTIFIER, image=IMAGE)


@pytest.mark.parametrize("operation", [rdc.restart, rdc.resume])
def test_ambiguous_start_error_still_attempts_exact_owned_cleanup(monkeypatch, capsys, operation):
    item = record()
    item["status"] = "suspended"
    apps = apps_with(item)
    monkeypatch.setattr(rdc, "checkpoint", lambda a: (record(), health(), 2))
    stop = Mock(return_value=item)
    monkeypatch.setattr(rdc, "stop_owned", stop)
    apps.start.side_effect = CloudProviderError("secret-timeout-detail", code="provider_http_error")
    with pytest.raises(CloudProviderError):
        operation(apps)
    assert stop.call_args == ((apps,), {"identifier": IDENTIFIER, "image": IMAGE})
    assert stop.call_count == (2 if operation is rdc.restart else 1)
    assert "secret-timeout-detail" not in capsys.readouterr().out
