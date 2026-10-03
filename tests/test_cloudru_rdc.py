"""Permanent RDC admission, ownership, private storage, and lifecycle contracts."""

import hashlib
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
TENANT = "59fea75a-3427-41b4-b2bd-a2bca66ad220"
NONCE = "b" * 64


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
        "quiesced": False,
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


def response(value, status=200):
    return {"statusCode": status, "body": json.dumps(value), "isBase64Encoded": False}


def closed_health(generation=2, **overrides):
    return health(
        **{
            "browser_ready": False,
            "rdc_running": False,
            "quiesced": True,
            "checkpoint_generation": generation,
            **overrides,
        }
    )


def private_store():
    store = Mock(_timeout=30)
    contents = {"owner.json": json.dumps(rdc.owner_marker(PROJECT)).encode()}
    store._request.return_value.content = acl()

    def download(name, **_kwargs):
        if name not in contents:
            raise StorageObjectNotFound("missing")
        return contents[name]

    store.download.side_effect = download
    store.upload.side_effect = lambda name, content, **kw: contents.update({name: content})
    return store


class Clock:
    def __init__(self):
        self.value = 0

    def __call__(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


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
        rdc.owned_record(apps, tenant=TENANT)
    apps.stop.assert_not_called()


@pytest.mark.parametrize("cpu,memory", [("1000m", "4Gi"), (1, 4294967296)])
def test_ownership_diagnostics_identify_encodings_without_accepting_them(cpu, memory, capsys):
    item = record()
    item["template"]["containers"][0]["resources"].update(cpu=cpu, memory=memory)
    apps = apps_with(item)
    with pytest.raises(CloudProviderError) as error:
        rdc.owned_record(apps, tenant=TENANT)
    assert error.value.code == "rdc_ownership_unconfirmed"
    diagnostic = json.loads(capsys.readouterr().out)
    assert diagnostic["fields"] == ["cpu", "memory"]
    assert diagnostic["cpu_form"] in {"thousand_millicores", "one_number"}
    assert diagnostic["memory_form"] in {"4_gib", "bytes_number"}
    apps.stop.assert_not_called()
    apps.start.assert_not_called()


def test_ownership_diagnostics_never_print_unknown_provider_values(capsys):
    item = record()
    secret = "arbitrary-private-provider-value"
    container = item["template"]["containers"][0]
    container["resources"].update(cpu=secret, memory=secret)
    container["resources"][secret] = secret
    container["env"].append({"name": secret, "value": secret})
    item["template"]["volumes"][0]["volumeAttributes"].update(
        entrypoint=secret, tenantId=secret, region=secret
    )
    with pytest.raises(CloudProviderError):
        rdc.owned_record(apps_with(item), tenant=TENANT)
    output = capsys.readouterr().out
    assert secret not in output
    diagnostic = json.loads(output)
    assert diagnostic["cpu_form"] == diagnostic["memory_form"] == "other"
    assert diagnostic["volume_entrypoint_form"] == "other"
    assert diagnostic["known_resource_keys"] == ["cpu", "memory"]
    assert diagnostic["known_platform_envs"] == []
    assert diagnostic["volume_tenant_match"] is False


def test_valid_ownership_emits_no_diagnostic(capsys):
    assert rdc.owned_record(apps_with(), tenant=TENANT)["id"] == IDENTIFIER
    assert capsys.readouterr().out == ""


def test_proto_defaults_and_expanded_managed_volume_do_not_break_ownership():
    item = record()
    item["template"]["containers"][0]["volumeMounts"][0].pop("readOnly")
    item["template"]["volumes"][0]["volumeAttributes"].update(
        entrypoint="https://s3.cloud.ru", region="ru-central-1", tenantId=TENANT
    )
    assert (
        rdc.owned_record(apps_with(item), tenant=TENANT, identifier=IDENTIFIER, image=IMAGE)["id"]
        == IDENTIFIER
    )
    with pytest.raises(CloudProviderError):
        rdc.owned_record(apps_with(item), tenant=TENANT, identifier=DEVICE)


def test_ambiguous_inventory_cannot_authorize_mutation():
    apps = apps_with()
    apps.list.return_value = [record(), record()]
    with pytest.raises(CloudProviderError):
        rdc.owned_record(apps, tenant=TENANT)


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
        rdc.test_call(apps, "/checkpoint", "POST", nonce=NONCE, timeout=300)
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
        rdc.install(apps, Mock(), object(), IMAGE, tenant=TENANT)
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
        rdc.install(apps, Mock(), object(), IMAGE, tenant=TENANT)
    stop.assert_called_once_with(apps, tenant=TENANT, identifier=IDENTIFIER, image=IMAGE)
    assert "secret-message" not in capsys.readouterr().out


def test_restart_requires_checkpoint_and_confirmed_suspension_before_start(monkeypatch):
    apps = apps_with()
    events = []
    monkeypatch.setattr(rdc, "checkpoint", lambda *a, **kw: (record(), health(), 2))
    monkeypatch.setattr(rdc, "stop_owned", lambda *a, **kw: events.append("suspended") or record())
    apps.start.side_effect = lambda *a: events.append("start")
    monkeypatch.setattr(
        rdc, "wait_ready", lambda *a, **kw: (record(), health(checkpoint_generation=2))
    )
    monkeypatch.setattr(rdc, "verify_anonymous_gate", Mock())
    result = rdc.restart(apps, private_store(), object(), tenant=TENANT)
    assert events == ["suspended", "start"]
    assert result["restart_verified"] and result["device_id"] == DEVICE


def test_restart_rejects_stale_or_changed_authorization_and_suspends(monkeypatch):
    apps = apps_with()
    monkeypatch.setattr(rdc, "checkpoint", lambda *a, **kw: (record(), health(), 2))
    stop = Mock(return_value=record())
    monkeypatch.setattr(rdc, "stop_owned", stop)
    monkeypatch.setattr(
        rdc,
        "wait_ready",
        lambda *a, **kw: (record(), health(device_id=PROJECT, checkpoint_generation=2)),
    )
    with pytest.raises(CloudProviderError) as error:
        rdc.restart(apps, private_store(), object(), tenant=TENANT)
    assert error.value.code == "rdc_restore_unconfirmed"
    assert stop.call_count == 2


def test_ambiguous_stop_never_starts_another_writer(monkeypatch):
    apps = apps_with()
    monkeypatch.setattr(rdc, "checkpoint", lambda *a, **kw: (record(), health(), 2))
    monkeypatch.setattr(
        rdc,
        "stop_owned",
        Mock(side_effect=CloudProviderError("unknown", code="rdc_cleanup_unconfirmed")),
    )
    with pytest.raises(CloudProviderError):
        rdc.restart(apps, private_store(), object(), tenant=TENANT)
    apps.start.assert_not_called()


def test_checkpoint_success_requires_new_monotonic_generation():
    apps = apps_with()
    apps.client.request.side_effect = lambda *a, **kw: response(
        health()
        if kw["json_body"]["method"] == "GET"
        else {"status": "CHECKPOINT_COMPLETE", "generation": 1}
    )
    with pytest.raises(CloudProviderError) as error:
        rdc.checkpoint(apps, private_store(), object(), tenant=TENANT)
    assert error.value.code == "rdc_checkpoint_unconfirmed"
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
    result = rdc.resume(apps, private_store(), object(), tenant=TENANT)
    apps.start.assert_called_once_with(item["name"])
    assert result["device_id"] == DEVICE
    apps.list.return_value = [record()]
    with pytest.raises(CloudProviderError) as error:
        rdc.resume(apps, private_store(), object(), tenant=TENANT)
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
        rdc.resume(apps, private_store(), object(), tenant=TENANT)
    stop.assert_called_once_with(apps, tenant=TENANT, identifier=IDENTIFIER, image=IMAGE)


@pytest.mark.parametrize("operation", [rdc.restart, rdc.resume])
def test_ambiguous_start_error_still_attempts_exact_owned_cleanup(monkeypatch, capsys, operation):
    item = record()
    item["status"] = "suspended"
    apps = apps_with(item)
    monkeypatch.setattr(rdc, "checkpoint", lambda *a, **kw: (record(), health(), 2))
    stop = Mock(return_value=item)
    monkeypatch.setattr(rdc, "stop_owned", stop)
    apps.start.side_effect = CloudProviderError("secret-timeout-detail", code="provider_http_error")
    with pytest.raises(CloudProviderError):
        operation(apps, private_store(), object(), tenant=TENANT)
    assert stop.call_args == ((apps,), {"tenant": TENANT, "identifier": IDENTIFIER, "image": IMAGE})
    assert stop.call_count == (2 if operation is rdc.restart else 1)
    assert "secret-timeout-detail" not in capsys.readouterr().out


@pytest.mark.parametrize("tenant", ["", "not-a-uuid", TENANT.upper()])
@pytest.mark.parametrize("action", ["preflight", "install", "status", "start", "restart", "stop"])
def test_every_action_requires_configured_canonical_tenant(action, tenant):
    apps, store = apps_with(), private_store()
    operations = {
        "preflight": lambda: rdc.preflight(apps, store, object(), tenant=tenant),
        "install": lambda: rdc.install(apps, store, object(), IMAGE, tenant=tenant),
        "status": lambda: rdc.status(apps, tenant=tenant),
        "start": lambda: rdc.resume(apps, store, object(), tenant=tenant),
        "restart": lambda: rdc.restart(apps, store, object(), tenant=tenant),
        "stop": lambda: rdc.suspend(apps, store, object(), tenant=tenant),
    }
    with pytest.raises(CloudProviderError):
        operations[action]()
    apps.list.assert_not_called()
    apps.client.request.assert_not_called()
    apps.start.assert_not_called()
    apps.stop.assert_not_called()
    store.upload.assert_not_called()


@pytest.mark.parametrize("action", ["preflight", "status", "start", "restart", "stop"])
def test_returned_mount_tenant_must_match_configured_tenant(action):
    item = record()
    item["template"]["volumes"][0]["volumeAttributes"]["tenantId"] = DEVICE
    apps, store = apps_with(item), private_store()
    operations = {
        "preflight": lambda: rdc.preflight(apps, store, object(), tenant=TENANT),
        "status": lambda: rdc.status(apps, tenant=TENANT),
        "start": lambda: rdc.resume(apps, store, object(), tenant=TENANT),
        "restart": lambda: rdc.restart(apps, store, object(), tenant=TENANT),
        "stop": lambda: rdc.suspend(apps, store, object(), tenant=TENANT),
    }
    with pytest.raises(CloudProviderError) as error:
        operations[action]()
    assert error.value.code == "rdc_ownership_unconfirmed"
    apps.client.request.assert_not_called()
    apps.start.assert_not_called()
    apps.stop.assert_not_called()
    store.upload.assert_not_called()


def test_s3_credentials_cannot_silently_select_another_tenant():
    with pytest.raises(CloudProviderError):
        rdc.storage_client(
            PROJECT,
            TENANT,
            env={"CLOUDRU_IAM_KEY_ID": DEVICE + ":key", "CLOUDRU_IAM_KEY_SECRET": "synthetic"},
        )
    _, credentials = rdc.storage_client(
        PROJECT,
        TENANT,
        env={"CLOUDRU_IAM_KEY_ID": "key", "CLOUDRU_IAM_KEY_SECRET": "synthetic"},
    )
    assert credentials.access_key_id == TENANT + ":key"


def test_control_permit_is_canonical_digest_only_and_header_carries_nonce(monkeypatch, capsys):
    apps, store = apps_with(), private_store()
    clock = Clock()
    monkeypatch.setattr(rdc.secrets, "token_hex", lambda size: NONCE if size == 32 else None)
    monkeypatch.setattr(rdc.time, "time", lambda: 1700000000)
    apps.client.request.side_effect = [
        response(health()),
        response({"status": "CHECKPOINT_COMPLETE", "generation": 2}),
        response(closed_health(), 503),
    ]
    _, durable, generation = rdc.checkpoint(
        apps,
        store,
        object(),
        tenant=TENANT,
        clock=clock,
        sleep=clock.sleep,
    )
    permit = {
        "action": "checkpoint",
        "container_name": rdc.names(PROJECT)[0],
        "expires_at": 1700000300,
        "issued_at": 1700000000,
        "project_id": PROJECT,
        "schema": 1,
        "sha256": hashlib.sha256(NONCE.encode("ascii")).hexdigest(),
    }
    raw = store.upload.call_args.args[1]
    assert store.upload.call_args.args[0] == "control-permit.json"
    assert raw == (json.dumps(permit, sort_keys=True, separators=(",", ":")) + "\n").encode()
    assert NONCE.encode() not in raw
    calls = [call.kwargs["json_body"] for call in apps.client.request.call_args_list]
    assert calls[1]["headers"] == {"X-Alice-Rdc-Control": NONCE}
    assert all("headers" not in body for body in (calls[0], calls[2]))
    assert durable["quiesced"] and generation == 2
    assert apps.client.timeout == 20 and store._timeout == 30
    assert capsys.readouterr().out == ""


def test_checkpoint_lost_response_after_durable_commit_recovers_same_snapshot():
    apps, store, clock = apps_with(), private_store(), Clock()
    apps.client.request.side_effect = [
        response(health()),
        CloudProviderError("synthetic lost response", code="provider_http_error"),
        response(closed_health(), 503),
        response({"status": "CHECKPOINT_COMPLETE", "generation": 2}),
        response(closed_health(), 503),
    ]
    _, durable, generation = rdc.checkpoint(
        apps,
        store,
        object(),
        tenant=TENANT,
        clock=clock,
        sleep=clock.sleep,
    )
    posts = [
        call.kwargs["json_body"]
        for call in apps.client.request.call_args_list
        if call.kwargs["json_body"]["method"] == "POST"
    ]
    assert generation == 2 and durable == closed_health()
    assert len(posts) == 2 and posts[0]["headers"] == posts[1]["headers"]
    store.upload.assert_called_once()
    apps.stop.assert_not_called()
    apps.start.assert_not_called()


def test_new_workflow_accepts_authenticated_cached_closed_generation():
    apps, store = apps_with(), private_store()
    apps.client.request.side_effect = [
        response(closed_health(), 503),
        response({"status": "CHECKPOINT_COMPLETE", "generation": 2}),
        response(closed_health(), 503),
    ]
    _, durable, generation = rdc.checkpoint(apps, store, object(), tenant=TENANT)
    assert generation == 2 and durable["quiesced"]
    store.upload.assert_called_once()


@pytest.mark.parametrize("body", ["", "Service unavailable", "{}", '"proxy"'])
def test_proxy_health_503_retries_without_proving_quiescence(body):
    apps, clock = apps_with(), Clock()
    apps.client.request.side_effect = [
        {"statusCode": 503, "body": body},
        response(health()),
    ]
    _, result = rdc.wait_ready(apps, tenant=TENANT, sleep=clock.sleep)
    assert result == health() and apps.client.request.call_count == 2


@pytest.mark.parametrize(
    "value",
    [
        closed_health(quiesced="true"),
        closed_health(generation=0),
        closed_health(browser_ready=True),
        closed_health(state_ready=False),
        closed_health(auth="synthetic"),
    ],
)
def test_claimed_runtime_503_requires_strict_closed_schema(value):
    apps, store = apps_with(), private_store()
    apps.client.request.return_value = response(value, 503)
    with pytest.raises(CloudProviderError):
        rdc.checkpoint(apps, store, object(), tenant=TENANT)
    apps.stop.assert_not_called()
    apps.start.assert_not_called()
    store.upload.assert_not_called()


def test_control_permit_readback_must_match_before_protected_post():
    apps, store, clock = apps_with(), private_store(), Clock()
    apps.client.request.return_value = response(health())
    download = store.download.side_effect
    store.download.side_effect = lambda name, **kw: (
        b"old permit" if name == rdc.CONTROL_FILE else download(name, **kw)
    )
    with pytest.raises(CloudProviderError) as error:
        rdc.checkpoint(apps, store, object(), tenant=TENANT, clock=clock, sleep=clock.sleep)
    assert error.value.code == "rdc_control_unconfirmed" and clock() == 10
    assert apps.client.request.call_count == 1
    apps.stop.assert_not_called()
    assert apps.client.timeout == 20 and store._timeout == 30


def test_permit_readback_request_obeys_visibility_deadline_and_rejects_late_success():
    apps, store, clock = apps_with(), private_store(), Clock()
    apps.client.request.return_value = response(health())
    download = store.download.side_effect

    def slow_download(name, **kwargs):
        if name == rdc.CONTROL_FILE:
            assert store._timeout <= 10 - clock()
            clock.sleep(store._timeout)
        return download(name, **kwargs)

    store.download.side_effect = slow_download
    with pytest.raises(CloudProviderError) as error:
        rdc.checkpoint(apps, store, object(), tenant=TENANT, clock=clock, sleep=clock.sleep)
    assert error.value.code == "rdc_control_unconfirmed" and clock() == 10
    assert apps.client.request.call_count == 1
    apps.stop.assert_not_called()
    assert apps.client.timeout == 20 and store._timeout == 30


def test_control_visibility_retry_uses_same_permit_and_nonce():
    apps, store, clock = apps_with(), private_store(), Clock()
    apps.client.request.side_effect = [
        response(health()),
        response({"status": "control_denied"}, 403),
        response({"status": "CHECKPOINT_COMPLETE", "generation": 2}),
        response(closed_health(), 503),
    ]
    assert (
        rdc.checkpoint(
            apps,
            store,
            object(),
            tenant=TENANT,
            clock=clock,
            sleep=clock.sleep,
        )[2]
        == 2
    )
    posts = [call.kwargs["json_body"] for call in apps.client.request.call_args_list][1:3]
    assert posts[0]["headers"] == posts[1]["headers"]
    store.upload.assert_called_once()
    assert clock() == 1


def test_control_denial_visibility_budget_is_ten_seconds_and_restores_timeouts():
    apps, store, clock = apps_with(), private_store(), Clock()
    apps.client.request.side_effect = lambda *a, **kw: response(
        health() if kw["json_body"]["method"] == "GET" else {"status": "control_denied"},
        200 if kw["json_body"]["method"] == "GET" else 403,
    )
    with pytest.raises(CloudProviderError) as error:
        rdc.checkpoint(apps, store, object(), tenant=TENANT, clock=clock, sleep=clock.sleep)
    assert error.value.code == "rdc_control_unconfirmed" and clock() == 10
    assert apps.client.timeout == 20 and store._timeout == 30
    apps.stop.assert_not_called()


def test_checkpoint_recovery_has_one_overall_deadline():
    apps, store, clock = apps_with(), private_store(), Clock()
    first = True

    def request(*_args, **kwargs):
        nonlocal first
        assert apps.client.timeout <= 5 - clock()
        if first:
            first = False
            return response(health())
        if kwargs["json_body"]["method"] == "POST":
            clock.sleep(3)
            raise CloudProviderError("synthetic timeout", code="provider_http_error")
        return {"statusCode": 503, "body": "proxy"}

    apps.client.request.side_effect = request
    with pytest.raises(CloudProviderError) as error:
        rdc.checkpoint(
            apps,
            store,
            object(),
            tenant=TENANT,
            timeout=5,
            clock=clock,
            sleep=clock.sleep,
        )
    assert error.value.code == "rdc_checkpoint_unconfirmed" and clock() == 5
    assert apps.client.timeout == 20 and store._timeout == 30
    apps.stop.assert_not_called()


def test_checkpoint_accepts_pairing_completed_during_close_but_preserves_existing_identity():
    apps, store = apps_with(), private_store()
    apps.client.request.side_effect = [
        response(health(paired=False, device_id=None)),
        response({"status": "CHECKPOINT_COMPLETE", "generation": 2}),
        response(closed_health(), 503),
    ]
    _, durable, _ = rdc.checkpoint(apps, store, object(), tenant=TENANT)
    assert durable["paired"] and durable["device_id"] == DEVICE
    apps.client.request.side_effect = [
        response(health()),
        response({"status": "CHECKPOINT_COMPLETE", "generation": 2}),
        response(closed_health(device_id=PROJECT), 503),
    ]
    with pytest.raises(CloudProviderError) as error:
        rdc.checkpoint(apps, store, object(), tenant=TENANT)
    assert error.value.code == "rdc_checkpoint_unconfirmed"
    apps.stop.assert_not_called()


def test_completed_quiesced_status_is_prompt_and_has_no_pairing_url():
    apps = apps_with()
    apps.client.request.return_value = response(closed_health(), 503)
    gate = Mock(return_value=SimpleNamespace(status_code=403, headers={}))
    result = rdc.status(apps, tenant=TENANT, http_get=gate)
    assert result["status"] == "RDC_QUIESCED" and result["quiesced"]
    assert "pairing_url" not in result and result["container_id"] == IDENTIFIER
    assert apps.client.request.call_count == 1


def test_stop_retry_of_confirmed_suspended_app_never_calls_runtime_or_mutates():
    item = record()
    item["status"] = "suspended"
    apps, store = apps_with(item), private_store()
    result = rdc.suspend(apps, store, object(), tenant=TENANT)
    assert result == {
        "status": "RDC_SUSPENDED",
        "container_name": item["name"],
        "container_id": IDENTIFIER,
    }
    apps.client.request.assert_not_called()
    apps.stop.assert_not_called()
    apps.start.assert_not_called()
    store.upload.assert_not_called()
    assert store.download.call_args.args[0] == "owner.json"


def test_gpu_default_diagnostic_does_not_accept_extra_resources(capsys):
    item = record()
    item["template"]["containers"][0]["resources"]["gpu"] = {"count": 0, "sku": ""}
    with pytest.raises(CloudProviderError):
        rdc.owned_record(apps_with(item), tenant=TENANT)
    diagnostic = json.loads(capsys.readouterr().out)
    assert diagnostic["fields"] == ["resource_keys"]
    assert diagnostic["known_resource_keys"] == ["cpu", "gpu", "memory"]
    assert diagnostic["gpu_form"] == "zero_count_empty_sku"
