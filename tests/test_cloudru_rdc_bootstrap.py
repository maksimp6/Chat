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
