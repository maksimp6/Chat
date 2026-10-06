from pathlib import Path

import pytest

from cloud.base import CloudProviderError
from scripts import cloudru_rdc_mcp_candidate as candidate


PROJECT = "22706bfa-6066-4e4e-a2f6-97a42b34f814"


class FakeClient:
    def __init__(self):
        self.requests = []

    def request(self, service, method, path, **kwargs):
        self.requests.append((service, method, path, kwargs))
        return {}


class FakeApps:
    def __init__(self, existing=None):
        self.existing = existing
        self.client = FakeClient()
        self.project_id = PROJECT
        self._status = {
            "public_uri": "rdc-mcp-22706bfa6066.containerapps.ru",
            "image": "registry/rdc-mcp@sha256:" + "a" * 64,
            "resources": {"cpu": "0.5", "memory": "1024Mi"},
            "scaling": {"minInstanceCount": 0, "maxInstanceCount": 1},
        }

    def get(self, name):
        assert name == "rdc-mcp-22706bfa6066"
        return self.existing

    def wait_until_ready(self, name, *, image, timeout_s, poll_s):
        assert name == "rdc-mcp-22706bfa6066"
        assert image == self._status["image"]
        assert timeout_s == 300
        assert poll_s == 5
        return self._status

    def health_check(self, public_uri, *, attempts, delay_s):
        assert public_uri == self._status["public_uri"]
        assert attempts == 18
        assert delay_s == 5
        return {"url": "https://" + public_uri + "/healthz"}


def test_candidate_name_is_distinct_from_persistent_rdc(monkeypatch):
    monkeypatch.setenv("CLOUDRU_PROJECT_ID", PROJECT)

    assert candidate.candidate_name(PROJECT) == "rdc-mcp-22706bfa6066"
    assert candidate.candidate_name(PROJECT) != "rdc-22706bfa6066"


def test_candidate_create_is_small_scale_to_zero_and_disables_native_auth(monkeypatch):
    monkeypatch.setenv("CLOUDRU_PROJECT_ID", PROJECT)
    apps = FakeApps()
    image = apps._status["image"]

    result = candidate.create_candidate(apps, image, "synthetic-token")

    assert result["status"] == "RDC_MCP_CANDIDATE_READY"
    _, method, path, kwargs = apps.client.requests[0]
    assert (method, path) == ("POST", "/v2/containers")
    body = kwargs["json_body"]
    assert body["name"] == "rdc-mcp-22706bfa6066"
    assert body["configuration"]["ingress"] == {
        "publiclyAccessible": True,
        "accessSettings": {"enableAuth": False},
    }
    assert body["template"]["scaling"] == {
        "minInstanceCount": 0,
        "maxInstanceCount": 1,
    }
    container = body["template"]["containers"][0]
    assert container["resources"] == {"cpu": "0.5", "memory": "1024Mi"}
    assert {item["name"] for item in container["env"]} == {"ALICE_RDC_MCP_TOKEN"}
    assert "synthetic-token" not in repr(result)


def test_candidate_refuses_to_take_over_existing_resource(monkeypatch):
    monkeypatch.setenv("CLOUDRU_PROJECT_ID", PROJECT)
    apps = FakeApps(existing={"id": "existing"})

    with pytest.raises(CloudProviderError) as error:
        candidate.create_candidate(apps, apps._status["image"], "synthetic-token")

    assert error.value.code == "already_exists"
    assert apps.client.requests == []


def test_lightweight_dockerfile_has_no_browser_packages():
    root = Path(__file__).resolve().parents[1]
    source = (root / "deploy" / "remote-desktop-commander" / "Dockerfile.mcp").read_text()

    assert "chromium" not in source.lower()
    assert "fonts-liberation" not in source.lower()
    assert "mcp-gateway.mjs" in source


def test_gateway_blocks_pdf_tool():
    root = Path(__file__).resolve().parents[1]
    source = (root / "deploy" / "remote-desktop-commander" / "mcp-gateway.mjs").read_text()

    assert 'new Set(["write_pdf"])' in source


def test_lightweight_image_is_alice_dev_ready_without_browser_or_db_server():
    root = Path(__file__).resolve().parents[1]
    deploy = root / "deploy" / "remote-desktop-commander"
    dockerfile = (deploy / "Dockerfile.mcp").read_text()
    bootstrap = (deploy / "dev-bootstrap.sh").read_text()

    assert "FROM python:3.14-slim-bookworm" in dockerfile
    assert "FROM node:22-bookworm-slim AS node-runtime" in dockerfile
    assert "chromium" not in dockerfile.lower()
    assert "postgresql" not in dockerfile.lower()
    assert "python -m pip" in dockerfile
    assert "node --version" in dockerfile
    assert "-r \"$repo/requirements.txt\"" in bootstrap
    assert "-r \"$repo/requirements-dev.txt\"" in bootstrap
    assert "npm install --ignore-scripts --no-audit --no-fund --package-lock=false" in bootstrap
    assert "bash scripts/format.sh check" in bootstrap
    assert 'import app; assert app.app.test_client().get("/healthz").status_code == 200' in bootstrap
