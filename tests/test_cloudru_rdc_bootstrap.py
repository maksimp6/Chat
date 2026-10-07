"""RDC minimum-resource regression contract; no provider calls."""

from scripts import cloudru_rdc as rdc

PROJECT = "94ae3a86-671f-40ae-9323-e81d3626135e"
IMAGE = "alice-rdc-probe.cr.cloud.ru/chromium-probe@sha256:" + "a" * 64


def test_rdc_uses_provider_minimum_resources():
    body = rdc.creation_body(PROJECT, IMAGE)
    container = body["template"]["containers"][0]
    assert container["resources"] == {"cpu": "0.1", "memory": "256Mi"}
    assert body["template"]["scaling"] == {"minInstanceCount": 1, "maxInstanceCount": 1}
