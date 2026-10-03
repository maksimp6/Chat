import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
GATEWAY_SPEC = ROOT / "deploy" / "cloudru" / "api-gateway" / "alice-openapi.example.json"
BROWSER_WORKER = ROOT / "deploy" / "chrome-worker"

BROWSER_ROUTES = {
    "/browser/v1/status": "get",
    "/browser/v1/navigate": "post",
    "/browser/v1/click": "post",
    "/browser/v1/type": "post",
    "/browser/v1/extract": "post",
    "/browser/v1/screenshot": "post",
}


@pytest.fixture(scope="module")
def gateway_spec():
    return json.loads(GATEWAY_SPEC.read_text(encoding="utf-8"))


def test_gateway_declares_explicit_browser_routes(gateway_spec):
    paths = gateway_spec["paths"]
    for route, method in BROWSER_ROUTES.items():
        assert route in paths, f"missing browser route {route}"
        assert method in paths[route], f"missing {method.upper()} {route}"


def test_browser_routes_use_dedicated_worker_backend(gateway_spec):
    backends = gateway_spec["components"]["x-cloud-backends"]
    assert "ChromeWorker" in backends
    backend = backends["ChromeWorker"]
    assert backend["type"] == "serverless_container"
    assert backend["nodes"][0]["container_id"] == "REPLACE_CHROME_WORKER_CONTAINER_APP_UUID"

    for route, method in BROWSER_ROUTES.items():
        operation = gateway_spec["paths"][route][method]
        assert operation["x-cloud-backend"]["$ref"] == (
            "#/components/x-cloud-backends/ChromeWorker"
        )


def test_browser_routes_have_machine_auth_and_limits(gateway_spec):
    for route, method in BROWSER_ROUTES.items():
        operation = gateway_spec["paths"][route][method]
        assert operation.get("security"), f"{route} must require Gateway machine auth"
        limit = operation.get("x-cloud-limit-count")
        assert isinstance(limit, dict), f"{route} must have an explicit rate limit"
        assert limit["count"] > 0
        assert limit["time_window"] > 0
        assert limit["rejected_code"] == 429


def test_gateway_does_not_publish_raw_cdp_or_catchall(gateway_spec):
    paths = set(gateway_spec["paths"])
    forbidden = {
        "/browser/{proxy+}",
        "/browser/{path}",
        "/cdp",
        "/json",
        "/json/version",
        "/devtools/{proxy+}",
    }
    assert paths.isdisjoint(forbidden)
    assert not any("cdp" in path.lower() or "devtools" in path.lower() for path in paths)


def test_browser_worker_contract_files_exist():
    assert (BROWSER_WORKER / "Dockerfile").is_file()
    assert (BROWSER_WORKER / "package.json").is_file()
    assert (BROWSER_WORKER / "server.mjs").is_file()


def test_worker_uses_google_chrome_and_playwright_not_rdc():
    dockerfile = (BROWSER_WORKER / "Dockerfile").read_text(encoding="utf-8").lower()
    package = json.loads((BROWSER_WORKER / "package.json").read_text(encoding="utf-8"))

    assert "google-chrome-stable" in dockerfile
    dependencies = package.get("dependencies", {})
    assert "playwright-core" in dependencies or "playwright" in dependencies

    combined = dockerfile + "\n" + json.dumps(package).lower()
    assert "desktop-commander" not in combined
    assert "remote-desktop-commander" not in combined


def test_worker_contract_has_no_raw_cdp_shell_or_arbitrary_eval():
    source = (BROWSER_WORKER / "server.mjs").read_text(encoding="utf-8").lower()

    for forbidden in (
        "/cdp",
        "/devtools",
        "child_process",
        "exec(",
        "spawn(",
        "evaluate(",
        "evaluatehandle(",
    ):
        assert forbidden not in source

    for route in BROWSER_ROUTES:
        assert route in source
