"""End-to-end browser resource coverage against a live Flask server."""

import json
import os
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright


BASE_URL = os.environ.get("ALICE_LIVE_BASE_URL", "http://127.0.0.1:5678").rstrip("/")
ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"


def local_static_files():
    ignored = {"sw.js"}
    return {
        "/" + path.relative_to(ROOT).as_posix()
        for path in STATIC.rglob("*")
        if path.is_file() and path.name not in ignored
    }


@pytest.mark.e2e
def test_browser_proves_resources_are_loaded_through_logical_ui_branches():
    expected = local_static_files()
    loaded = set()
    failures = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()

        def on_response(response):
            path = response.url.split("?", 1)[0]
            if path.startswith(BASE_URL + "/static/"):
                relative = path[len(BASE_URL):]
                if response.status == 200:
                    loaded.add(relative)
                else:
                    failures.append((relative, response.status))

        page.on("response", on_response)
        page.goto(BASE_URL + "/", wait_until="networkidle", timeout=30_000)

        # Give deferred/async scripts and initialization branches a chance to run.
        page.wait_for_timeout(1000)

        # Exercise visible controls. A branch may legitimately return an API
        # error in CI, but it must not prevent the browser from traversing the
        # UI and observing resource loads.
        controls = page.locator("button:visible")
        count = min(controls.count(), 100)
        for index in range(count):
            try:
                control = page.locator("button:visible").nth(index)
                if not control.is_enabled():
                    continue
                control.click(timeout=1500, no_wait_after=True)
                page.wait_for_timeout(100)
                page.keyboard.press("Escape")
            except Exception:
                continue

        # Exercise links as a second logical path without navigating away.
        links = page.locator("a[href]:visible")
        for index in range(min(links.count(), 50)):
            try:
                href = links.nth(index).get_attribute("href") or ""
                if href.startswith("/static/"):
                    page.request.get(BASE_URL + href, timeout=5000)
            except Exception:
                continue

        page.wait_for_timeout(1000)
        browser.close()

    assert not failures, f"static resources returned non-200 responses: {failures}"

    missing = sorted(expected - loaded)
    assert not missing, (
        "static resources were not proven loaded by the live browser logical flows: "
        + json.dumps(missing, ensure_ascii=False)
    )
