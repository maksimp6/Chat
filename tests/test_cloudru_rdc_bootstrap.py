"""Bootstrap RDC resource contract for #939; no provider calls."""

from scripts import cloudru_rdc as rdc

PROJECT = "94ae3a86-671f-40ae-9323-e81d3626135e"
IMAGE = "alice-rdc-probe.cr.cloud.ru/chromium-probe@sha256:" + "a" * 64


def test_bootstrap_profile_is_bounded_to_half_cpu_and_512_mib():
    body = rdc.creation_body(PROJECT, IMAGE, profile="bootstrap")
    container = body["template"]["containers"][0]
    assert container["resources"] == {"cpu": "0.5", "memory": "512Mi"}
    assert body["template"]["scaling"] == {"minInstanceCount": 1, "maxInstanceCount": 1}


def test_default_persistent_profile_is_unchanged():
    body = rdc.creation_body(PROJECT, IMAGE)
    container = body["template"]["containers"][0]
    assert container["resources"] == {"cpu": "1", "memory": "4096Mi"}
    assert body["template"]["scaling"] == {"minInstanceCount": 1, "maxInstanceCount": 1}


def test_bootstrap_install_passes_profile_to_creation_body(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock

    apps = SimpleNamespace(project_id=PROJECT, client=Mock())
    apps.client.request.return_value = {"resourceId": None}
    monkeypatch.setattr(rdc, "named_record", lambda _apps: None)
    monkeypatch.setattr(rdc, "prepare_bucket", Mock())
    create = Mock(return_value={"name": "bootstrap"})
    monkeypatch.setattr(rdc, "creation_body", create)
    monkeypatch.setattr(rdc, "wait_ready", Mock(side_effect=RuntimeError("stop-after-create")))
    monkeypatch.setattr(rdc, "stop_owned", Mock(return_value={"id": "00000000-0000-0000-0000-000000000000"}))

    try:
        rdc.install(apps, Mock(), object(), IMAGE, tenant="59fea75a-3427-41b4-b2bd-a2bca66ad220", profile="bootstrap")
    except RuntimeError as exc:
        assert str(exc) == "stop-after-create"
    create.assert_called_once_with(PROJECT, IMAGE, profile="bootstrap")
