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
        "method": "get",
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
        if kw["json_body"]["method"] == "get"
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
        if call.kwargs["json_body"]["method"] == "post"
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
        health() if kw["json_body"]["method"] == "get" else {"status": "control_denied"},
        200 if kw["json_body"]["method"] == "get" else 403,
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
        if kwargs["json_body"]["method"] == "post":
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


@pytest.mark.parametrize("gpu", [{"count": False}, {"count": 0.0, "sku": ""}])
def test_gpu_diagnostic_requires_nested_integer_zero(gpu, capsys):
    item = record()
    item["template"]["containers"][0]["resources"]["gpu"] = gpu
    with pytest.raises(CloudProviderError):
        rdc.owned_record(apps_with(item), tenant=TENANT)
    diagnostic = json.loads(capsys.readouterr().out)
    assert diagnostic["fields"] == ["resource_keys"]
    assert diagnostic["gpu_form"] == "other"


@pytest.mark.parametrize("present", [False, True])
def test_entrypoint_diagnostic_distinguishes_omitted_from_null(present, capsys):
    item = record()
    item["template"]["containers"][0]["resources"]["cpu"] = "1000m"
    if present:
        item["template"]["volumes"][0]["volumeAttributes"]["entrypoint"] = None
    with pytest.raises(CloudProviderError):
        rdc.owned_record(apps_with(item), tenant=TENANT)
    diagnostic = json.loads(capsys.readouterr().out)
    assert diagnostic["volume_entrypoint_form"] == ("null" if present else "omitted")
    assert ("volume" in diagnostic["fields"]) is present


def test_volume_attribute_diagnostics_allowlist_keys_without_values(capsys):
    item = record()
    secret = "unknown-private-volume-value"
    attributes = item["template"]["volumes"][0]["volumeAttributes"]
    attributes.update(readOnly="false", accessKeyId=secret, secretAccessKey=secret)
    attributes[secret] = secret
    with pytest.raises(CloudProviderError):
        rdc.owned_record(apps_with(item), tenant=TENANT)
    output = capsys.readouterr().out
    assert secret not in output
    diagnostic = json.loads(output)
    assert diagnostic["fields"] == ["volume"]
    assert diagnostic["known_volume_attributes"] == [
        "accessKeyId",
        "bucketName",
        "readOnly",
        "secretAccessKey",
    ]
    assert diagnostic["volume_read_only_form"] == "false_string"


@pytest.mark.parametrize("value", [False, "false", "False", "FALSE"])
def test_explicit_global_writable_volume_preserves_ownership(value):
    item = record()
    item["template"]["volumes"][0]["volumeAttributes"]["readOnly"] = value
    assert rdc.owned_record(apps_with(item), tenant=TENANT)["id"] == IDENTIFIER


@pytest.mark.parametrize(
    "value", [None, "", 0, 0.0, "0", True, "true", "True", "TRUE", 1, "1", "unknown"]
)
def test_global_readonly_ambiguous_or_enabled_is_not_owned(value):
    item = record()
    item["template"]["volumes"][0]["volumeAttributes"]["readOnly"] = value
    apps = apps_with(item)
    with pytest.raises(CloudProviderError) as error:
        rdc.owned_record(apps, tenant=TENANT)
    assert error.value.code == "rdc_ownership_unconfirmed"
    apps.stop.assert_not_called()
    apps.start.assert_not_called()


def test_global_writable_does_not_override_readonly_mount():
    item = record()
    item["template"]["volumes"][0]["volumeAttributes"]["readOnly"] = "False"
    item["template"]["containers"][0]["volumeMounts"][0]["readOnly"] = True
    with pytest.raises(CloudProviderError):
        rdc.owned_record(apps_with(item), tenant=TENANT)


def transport_error(grpc_code=3):
    response_value = requests.Response()
    response_value.status_code = 400
    response_value._content = json.dumps({"code": grpc_code}).encode()
    error = CloudProviderError("private", code="provider_http_error", http_status=400)
    try:
        raise error from requests.HTTPError(response=response_value)
    except CloudProviderError as caught:
        return caught


def test_health_compatibility_fallback_is_fixed_and_shares_timeout(monkeypatch, capsys):
    apps = apps_with()
    original = transport_error()
    timeouts = []

    def call(*_args, **_kwargs):
        timeouts.append(apps.client.timeout)
        if len(timeouts) == 1:
            raise original
        return response(health())

    apps.client.request.side_effect = call
    ticks = iter([10, 13])
    monkeypatch.setattr(rdc.time, "monotonic", lambda: next(ticks))
    assert rdc.test_call(apps, "/healthz") == health()
    calls = apps.client.request.call_args_list
    assert timeouts == [20, 17]
    assert calls[1].args == (
        "container_apps",
        "POST",
        f"/v2/containers/{rdc.names(PROJECT)[0]}/testCall",
    )
    assert calls[1].kwargs == {
        "params": {"projectId": PROJECT, "method": "get", "path": "/healthz"}
    }
    assert apps.client.timeout == 20
    diagnostic = json.loads(capsys.readouterr().out)
    assert diagnostic == {
        "stage": "rdc_health_compatibility",
        "error": "provider_http_error",
        "http_status": 400,
        "provider_status_code": 3,
    }


@pytest.mark.parametrize("status", [401, 403, 404, 500])
def test_health_auth_and_other_transport_errors_never_fallback(status):
    apps = apps_with()
    apps.client.request.side_effect = CloudProviderError(
        "private", code="provider_http_error", http_status=status
    )
    with pytest.raises(CloudProviderError):
        rdc.test_call(apps, "/healthz")
    assert apps.client.request.call_count == 1
    assert apps.client.timeout == 20


def test_checkpoint_transport_400_never_uses_headerless_fallback():
    apps = apps_with()
    apps.client.request.side_effect = CloudProviderError(
        "private", code="provider_http_error", http_status=400
    )
    with pytest.raises(CloudProviderError):
        rdc.test_call(apps, "/checkpoint", "POST", nonce=NONCE)
    assert apps.client.request.call_count == 1
    assert apps.client.request.call_args.kwargs["json_body"]["headers"] == {
        rdc.CONTROL_HEADER: NONCE
    }


def test_runtime_400_never_uses_transport_fallback():
    apps = apps_with()
    apps.client.request.return_value = response({"error": "private"}, status=400)
    with pytest.raises(CloudProviderError):
        rdc.test_call(apps, "/healthz")
    assert apps.client.request.call_count == 1


def test_health_fallback_does_not_extend_expired_timeout(monkeypatch):
    apps = apps_with()
    original = transport_error()
    apps.client.request.side_effect = original
    ticks = iter([10, 31])
    monkeypatch.setattr(rdc.time, "monotonic", lambda: next(ticks))
    with pytest.raises(CloudProviderError) as error:
        rdc.test_call(apps, "/healthz")
    assert error.value is original
    assert apps.client.request.call_count == 1
    assert apps.client.timeout == 20


def test_safe_provider_testcall_diagnostics_exclude_raw_values():
    secret = "opaque-private-value-XYZ987"
    response_value = requests.Response()
    response_value._content = json.dumps(
        {
            "code": 3,
            "message": "unsupported method " + secret,
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.BadRequest",
                    "fieldViolations": [
                        {"field": "method", "description": secret},
                        {"field": secret},
                    ],
                }
            ],
        }
    ).encode()
    request_error = requests.HTTPError(response=response_value)
    error = CloudProviderError(secret, code="provider_http_error", http_status=400)
    try:
        raise error from request_error
    except CloudProviderError as caught:
        diagnostic = rdc.safe_error(caught)
    assert diagnostic["provider_validation_fields"] == ["method"]
    assert diagnostic["provider_error_terms"] == ["method", "private", "unsupported"]
    assert secret not in json.dumps(diagnostic)


def test_status_reports_only_verified_service_and_known_resource_state(capsys):
    item = record()
    item["status"] = "untrusted-private-state"
    apps = apps_with(item)
    apps.client.request.side_effect = CloudProviderError(
        "private", code="provider_http_error", http_status=403
    )
    with pytest.raises(CloudProviderError):
        rdc.status(apps, tenant=TENANT)
    diagnostic = json.loads(capsys.readouterr().out)
    assert diagnostic == {
        "stage": "rdc_owned_service",
        "container_name": rdc.names(PROJECT)[0],
        "container_id": IDENTIFIER,
        "resource_state": "other",
    }


@pytest.mark.parametrize("grpc_code", [7, 16, None, "3", True, 2, 99])
def test_http400_authorization_or_unknown_grpc_never_fallback(grpc_code):
    apps = apps_with()
    apps.client.request.side_effect = transport_error(grpc_code)
    with pytest.raises(CloudProviderError):
        rdc.test_call(apps, "/healthz")
    assert apps.client.request.call_count == 1
    assert apps.client.timeout == 20


def revision_detail(**overrides):
    return {
        "id": DEVICE,
        "projectId": PROJECT,
        "serverlessId": IDENTIFIER,
        "status": "error",
        "statusReason": "ErrImagePull opaque-private-value",
        **overrides,
    }


def test_revision_diagnostics_bind_context_and_hide_provider_reason(capsys):
    apps = apps_with()
    apps.client.request.side_effect = [{"data": [{"id": DEVICE}]}, revision_detail(), {"data": []}]
    rdc.revision_diagnostics(apps, record())
    diagnostic, system = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert system["stage"] == "rdc_system_event_diagnostic" and system["events_examined"] == 0
    assert diagnostic == {
        "stage": "rdc_revision_diagnostic",
        "revision_id": DEVICE,
        "resource_state": "error",
        "reason_categories": ["image_pull"],
        "reason_form": "text",
        "reason_terms": ["image", "pull"],
    }
    assert apps.client.timeout == 20
    assert apps.client.request.call_args_list[0].kwargs == {
        "params": {"projectId": PROJECT, "pageSize": "3"}
    }
    assert apps.client.request.call_args_list[1].args == (
        "container_apps",
        "GET",
        f"/v2/containers/{rdc.names(PROJECT)[0]}/revisions/{DEVICE}",
    )


@pytest.mark.parametrize(
    "field,value", [("id", IDENTIFIER), ("projectId", DEVICE), ("serverlessId", DEVICE)]
)
def test_revision_diagnostics_reject_cross_context_details(field, value, capsys):
    apps = apps_with()
    apps.client.request.side_effect = [
        {"data": [{"id": DEVICE}]},
        revision_detail(**{field: value}),
    ]
    with pytest.raises(CloudProviderError):
        rdc.revision_diagnostics(apps, record())
    assert capsys.readouterr().out == ""
    assert apps.client.timeout == 20


def test_revision_diagnostics_limit_requests_and_total_timeout(capsys):
    apps = apps_with()
    clock = Clock()
    timeouts = []

    def read(*_args, **_kwargs):
        timeouts.append(apps.client.timeout)
        if len(timeouts) == 1:
            clock.sleep(50)
            return {"data": [{"id": DEVICE}]}
        if len(timeouts) == 2:
            return revision_detail(status="private-state", statusReason="private reason")
        return {"data": []}

    apps.client.request.side_effect = read
    rdc.revision_diagnostics(apps, record(), clock=clock)
    assert timeouts == [20, 10, 10]
    assert apps.client.timeout == 20
    diagnostic, system = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert diagnostic["resource_state"] == "other"
    assert diagnostic["reason_categories"] == ["other"]
    assert system["events_examined"] == 0


def test_revision_diagnostics_stop_when_deadline_expires():
    apps = apps_with()
    clock = Clock()

    def read(*_args, **_kwargs):
        clock.sleep(60)
        return {"data": [{"id": DEVICE}]}

    apps.client.request.side_effect = read
    with pytest.raises(CloudProviderError):
        rdc.revision_diagnostics(apps, record(), clock=clock)
    assert apps.client.request.call_count == 1
    assert apps.client.timeout == 20


def test_revision_diagnostics_reject_excessive_inventory():
    apps = apps_with()
    apps.client.request.return_value = {"data": [{"id": DEVICE}] * 4}
    with pytest.raises(CloudProviderError):
        rdc.revision_diagnostics(apps, record())
    assert apps.client.request.call_count == 1
    assert apps.client.timeout == 20


def test_method_normalization_preserves_checkpoint_nonce_and_internal_allowlist():
    apps = apps_with()
    apps.client.request.return_value = response({"checkpoint_generation": 2})
    assert rdc.test_call(apps, "/checkpoint", "POST", nonce=NONCE) == {"checkpoint_generation": 2}
    body = apps.client.request.call_args.kwargs["json_body"]
    assert body["method"] == "post"
    assert body["headers"] == {rdc.CONTROL_HEADER: NONCE}
    with pytest.raises(CloudProviderError):
        rdc.test_call(apps, "/healthz", "get")


def test_system_event_diagnostics_bind_context_and_hide_unknown_values(capsys):
    apps = apps_with()
    secret = "opaque-private-value-XYZ987"
    events = [
        {
            "serverlessId": IDENTIFIER,
            "reason": "FailedMount",
            "message": "volume permission denied " + secret,
        },
        {"serverlessId": IDENTIFIER, "reason": secret, "message": secret},
    ]
    apps.client.request.side_effect = [{"data": []}, {"data": events}]
    rdc.revision_diagnostics(apps, record())
    output = capsys.readouterr().out
    assert secret not in output
    diagnostic = json.loads(output)
    assert diagnostic["known_reasons"] == ["FailedMount", "other"]
    assert diagnostic["event_terms"] == ["denied", "failed", "mount", "permission", "volume"]
    assert apps.client.request.call_args.args == (
        "container_apps",
        "GET",
        f"/v2/containers/{rdc.names(PROJECT)[0]}/systemLogs",
    )
    assert apps.client.request.call_args.kwargs == {
        "params": {"projectId": PROJECT, "serverlessId": IDENTIFIER}
    }
    assert apps.client.timeout == 20


@pytest.mark.parametrize("owner", [None, DEVICE])
def test_system_events_reject_missing_or_foreign_resource_context(owner, capsys):
    apps = apps_with()
    apps.client.request.side_effect = [
        {"data": []},
        {"data": [{"serverlessId": owner, "reason": "FailedMount"}]},
    ]
    with pytest.raises(CloudProviderError):
        rdc.revision_diagnostics(apps, record())
    assert capsys.readouterr().out == ""
    assert apps.client.timeout == 20


def test_system_events_cap_processing_and_report_truncation(capsys):
    apps = apps_with()
    event = {"serverlessId": IDENTIFIER, "reason": "Started", "message": "container started"}
    apps.client.request.side_effect = [{"data": []}, {"data": [event] * 101}]
    rdc.revision_diagnostics(apps, record())
    diagnostic = json.loads(capsys.readouterr().out)
    assert diagnostic["events_examined"] == 100
    assert diagnostic["response_truncated"] is True
    assert diagnostic["known_reasons"] == ["Started"]
    assert apps.client.request.call_count == 2


def test_system_events_share_revision_diagnostic_deadline(capsys):
    apps = apps_with()
    clock = Clock()
    timeouts = []

    def read(*_args, **_kwargs):
        timeouts.append(apps.client.timeout)
        clock.sleep(50 if len(timeouts) == 1 else 10)
        return {"data": []}

    apps.client.request.side_effect = read
    with pytest.raises(CloudProviderError):
        rdc.revision_diagnostics(apps, record(), clock=clock)
    assert timeouts == [20, 10]
    assert capsys.readouterr().out == ""
    assert apps.client.timeout == 20


@pytest.mark.parametrize(
    "form,value",
    [
        ("missing", "missing"),
        ("null", None),
        ("empty", ""),
        ("text", "opaque-private-value"),
        ("oversize", "x" * 8193),
    ],
)
def test_revision_reason_shape_is_fixed_and_values_hidden(form, value, capsys):
    apps = apps_with()
    detail = revision_detail(statusReason=value)
    if form == "missing":
        detail.pop("statusReason")
    apps.client.request.side_effect = [{"data": [{"id": DEVICE}]}, detail, {"data": []}]
    rdc.revision_diagnostics(apps, record())
    diagnostic = json.loads(capsys.readouterr().out.splitlines()[0])
    assert diagnostic["reason_form"] == form
    assert diagnostic["reason_terms"] == []
