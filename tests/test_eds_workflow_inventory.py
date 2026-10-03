import json
from pathlib import Path
from uuid import UUID

import pytest
import yaml

from scripts import eds_workflow_inventory as module


def application(number, **overrides):
    return {"id": str(UUID(int=number)), "name": "rdc-browser", "status": "running", **overrides}


def test_complete_pagination_and_output_allowlist():
    calls = []

    def query(offset, timeout):
        calls.append(offset)
        assert 0 < timeout <= 29
        rows = [application(i) for i in range(offset + 1, min(offset + 50, 51) + 1)]
        for row in rows:
            row["environment"] = {"token": "secret-sentinel"}
            row["repository_url"] = "https://user:secret-sentinel@example.com"
        return {"applications": rows, "total": 51}

    report = module.inventory(query)
    assert calls == [0, 50]
    assert report["complete"] and report["count"] == 51
    assert "secret-sentinel" not in json.dumps(report)


@pytest.mark.parametrize(
    "page",
    [
        {"applications": [application(1), application(1)], "total": 2},
        {"applications": [], "total": 1},
        {"applications": [application(1)], "total": 0},
        {"applications": [], "total": True},
        {"applications": [{"id": "not-an-id"}]},
    ],
)
def test_rejects_incomplete_or_ambiguous_pages(page):
    with pytest.raises(ValueError):
        module.inventory(lambda *_: page)


def test_one_deadline_for_all_pages():
    times = iter([0, 0, 30])
    with pytest.raises(TimeoutError):
        module.inventory(
            lambda *_: {"applications": [application(i) for i in range(50)]},
            clock=lambda: next(times),
        )


def test_missing_applications_does_not_report_complete_inventory():
    with pytest.raises(ValueError):
        module.inventory(lambda *_: {})
    pages = iter([{"applications": [application(i) for i in range(50)]}, {}])
    with pytest.raises(ValueError):
        module.inventory(lambda *_: next(pages))
    assert module.inventory(lambda *_: {"total": 0}) == {
        "complete": True,
        "count": 0,
        "applications": [],
    }


def test_secret_echoes_and_cli_errors_are_not_published(monkeypatch, capsys):
    secret = "secret-sentinel"
    monkeypatch.setenv("EDS_API_KEY", secret)
    monkeypatch.setenv("EDS_PROJECT_ID", "project")
    monkeypatch.setattr(
        module,
        "query_page",
        lambda *_: {"applications": [application(1, name=secret, branch=secret, status=secret)]},
    )
    assert module.main() == 0
    assert secret not in capsys.readouterr().out

    def failed(*_):
        raise RuntimeError(secret)

    monkeypatch.setattr(module, "query_page", failed)
    assert module.main() == 1
    output = capsys.readouterr()
    assert secret not in output.out + output.err
    assert "failed" in output.out


def test_workflow_limits_credentials_to_trusted_inventory():
    path = Path(__file__).resolve().parents[1] / ".github/workflows/eds-workflow-inventory.yml"
    config = yaml.safe_load(path.read_text())
    assert config[True] == {"workflow_dispatch": None}
    assert config["permissions"] == {"contents": "read"}
    job = config["jobs"]["inventory"]
    assert job["if"] == "github.ref == 'refs/heads/master'"
    assert job["environment"] == "production"
    assert job["steps"][0]["with"]["ref"] == "${{ github.sha }}"
    assert all("EDS_API_KEY" not in str(step) for step in job["steps"][:-1])
    assert job["steps"][-1]["run"] == "timeout 30s python scripts/eds_workflow_inventory.py"
