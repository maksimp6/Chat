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
        self.updates = []
        self._status = {
            "id": "11111111-1111-1111-1111-111111111111",
            "public_uri": "alice-dev-22706bfa6066.containerapps.ru",
            "image": "registry/alice-dev@sha256:" + "a" * 64,
            "resources": {"cpu": "0.2", "memory": "512Mi"},
            "scaling": {"minInstanceCount": 0, "maxInstanceCount": 1},
        }

    def find_for_deploy(self, name):
        assert name == "alice-dev-22706bfa6066-aaaaaaaa"
        return self.existing

    def update_from_current(self, spec, current):
        self.updates.append((spec, current))
        return {}

    def wait_until_ready(self, name, *, image, timeout_s, poll_s):
        assert name == "alice-dev-22706bfa6066-aaaaaaaa"
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

    assert (
        candidate.candidate_name(PROJECT, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
        == "alice-dev-22706bfa6066-aaaaaaaa"
    )
    assert (
        candidate.candidate_name(PROJECT, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
        != "rdc-22706bfa6066"
    )


def test_candidate_create_is_small_scale_to_zero_and_disables_native_auth(monkeypatch):
    monkeypatch.setenv("CLOUDRU_PROJECT_ID", PROJECT)
    apps = FakeApps()
    image = apps._status["image"]

    result = candidate.create_candidate(
        apps, image, "synthetic-token", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    )

    assert result["status"] == "ALICE_DEV_READY"
    _, method, path, kwargs = apps.client.requests[0]
    assert (method, path) == ("POST", "/v2/containers")
    body = kwargs["json_body"]
    assert body["name"] == "alice-dev-22706bfa6066-aaaaaaaa"
    assert body["configuration"]["ingress"] == {
        "publiclyAccessible": True,
        "accessSettings": {"enableAuth": False},
    }
    assert body["template"]["scaling"] == {
        "minInstanceCount": 0,
        "maxInstanceCount": 1,
    }
    container = body["template"]["containers"][0]
    assert container["resources"] == {"cpu": "0.2", "memory": "512Mi"}
    assert {item["name"] for item in container["env"]} == {"ALICE_SHORT_TOKEN"}
    assert "synthetic-token" not in repr(result)


def test_candidate_updates_existing_alice_dev_in_place(monkeypatch):
    monkeypatch.setenv("CLOUDRU_PROJECT_ID", PROJECT)
    existing = {
        "id": "existing",
        "name": "alice-dev-22706bfa6066-aaaaaaaa",
        "description": candidate.DESCRIPTION,
    }
    apps = FakeApps(existing=existing)

    result = candidate.create_candidate(
        apps, apps._status["image"], "synthetic-token", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    )

    assert result["status"] == "ALICE_DEV_READY"
    assert apps.client.requests == []
    assert len(apps.updates) == 1
    spec, current = apps.updates[0]
    assert current is existing
    assert spec.name == "alice-dev-22706bfa6066-aaaaaaaa"
    assert spec.image == apps._status["image"]


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


def test_candidate_script_runs_directly_from_repo_root():
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "scripts/cloudru_rdc_mcp_candidate.py", "--help"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "Deploy an isolated lightweight Alice Dev candidate" in result.stdout


def test_candidate_refuses_mismatched_existing_resource(monkeypatch):
    monkeypatch.setenv("CLOUDRU_PROJECT_ID", PROJECT)
    apps = FakeApps(
        existing={
            "name": "alice-dev-22706bfa6066-aaaaaaaa",
            "description": "some other service",
        }
    )

    with pytest.raises(CloudProviderError) as error:
        candidate.create_candidate(
            apps,
            apps._status["image"],
            "synthetic-token",
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        )

    assert error.value.code == "resource_mismatch"
    assert apps.updates == []


def test_candidate_uses_ten_second_idle_timeout(monkeypatch):
    monkeypatch.setenv("CLOUDRU_PROJECT_ID", PROJECT)
    apps = FakeApps()
    sha = "a" * 40

    candidate.create_candidate(apps, apps._status["image"], "synthetic-token", sha)

    body = apps.client.requests[0][3]["json_body"]
    assert body["template"]["idleTimeout"] == "10s"


def test_alice_dev_workflow_serializes_only_same_commit():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "cloudru-rdc-mcp-candidate.yml").read_text()

    assert "group: alice-cloudru-alice-dev-${{ github.sha }}" in workflow
    assert "cancel-in-progress: true" in workflow


def test_accepted_container_manifest_is_uploaded_by_commit():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "cloudru-rdc-mcp-candidate.yml").read_text()

    assert '"container_id": r["container_id"]' in workflow
    assert "name: alice-dev-container-${{ github.sha }}" in workflow
    assert "Upload accepted container manifest" in workflow
    assert workflow.index("Verify public MCP end to end") < workflow.index(
        "Upload accepted container manifest"
    )


class TombstoneApps:
    def __init__(self, inventories):
        self.inventories = list(inventories)
        self.deleted = []

    def list(self, *, require_total):
        assert require_total is True
        if len(self.inventories) > 1:
            return self.inventories.pop(0)
        return self.inventories[0]

    def delete(self, name):
        self.deleted.append(name)
        return {}


def test_tombstone_id_is_deleted_and_verified_absent(monkeypatch, tmp_path):
    identifier = "11111111-1111-1111-1111-111111111111"
    tombstones = tmp_path / "delete.txt"
    tombstones.write_text(identifier + "\n")
    monkeypatch.setattr(candidate, "TOMBSTONES", tombstones)
    monkeypatch.setenv("CLOUDRU_PROJECT_ID", PROJECT)
    apps = TombstoneApps(
        [
            [{"id": identifier, "name": "alice-dev-old"}],
            [],
        ]
    )

    assert candidate.purge_tombstones(apps) == [identifier]
    assert apps.deleted == ["alice-dev-old"]


def test_tombstone_that_is_already_absent_is_a_noop(monkeypatch, tmp_path):
    tombstones = tmp_path / "delete.txt"
    tombstones.write_text("11111111-1111-1111-1111-111111111111\n")
    monkeypatch.setattr(candidate, "TOMBSTONES", tombstones)
    monkeypatch.setenv("CLOUDRU_PROJECT_ID", PROJECT)
    apps = TombstoneApps([[]])

    assert candidate.purge_tombstones(apps) == []
    assert apps.deleted == []


def test_tombstone_survivor_fails_closed(monkeypatch, tmp_path):
    identifier = "11111111-1111-1111-1111-111111111111"
    tombstones = tmp_path / "delete.txt"
    tombstones.write_text(identifier + "\n")
    monkeypatch.setattr(candidate, "TOMBSTONES", tombstones)
    monkeypatch.setenv("CLOUDRU_PROJECT_ID", PROJECT)
    item = {"id": identifier, "name": "alice-dev-old"}
    apps = TombstoneApps([[item], [item]])

    with pytest.raises(CloudProviderError) as error:
        candidate.purge_tombstones(apps)

    assert error.value.code == "tombstone_survived"


def test_inventory_reports_all_alice_dev_public_addresses():
    apps = TombstoneApps(
        [
            [
                {
                    "id": "11111111-1111-1111-1111-111111111111",
                    "name": "alice-dev-a",
                    "status": "RUNNING",
                    "configuration": {"ingress": {"publicUri": "a.containerapps.ru"}},
                    "template": {"containers": [{"image": "registry/a"}]},
                },
                {
                    "id": "22222222-2222-2222-2222-222222222222",
                    "name": "alice-dev-b",
                    "status": "IDLE",
                    "configuration": {"ingress": {"publicUri": "b.containerapps.ru"}},
                    "template": {"containers": [{"image": "registry/b"}]},
                },
                {"id": "other", "name": "not-alice"},
            ]
        ]
    )

    assert candidate.alice_dev_inventory(apps) == [
        {
            "id": "11111111-1111-1111-1111-111111111111",
            "name": "alice-dev-a",
            "origin": "https://a.containerapps.ru",
            "image": "registry/a",
            "status": "RUNNING",
        },
        {
            "id": "22222222-2222-2222-2222-222222222222",
            "name": "alice-dev-b",
            "origin": "https://b.containerapps.ru",
            "image": "registry/b",
            "status": "IDLE",
        },
    ]
