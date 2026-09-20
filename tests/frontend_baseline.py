"""Automated frontend baseline probe.

Usage (Windows PowerShell):
  py -m pip install -r tests/requirements-frontend.txt
  py -m playwright install chromium
  $env:BASE_URL = "https://example.test/preview/pr-228/"
  py tests/frontend_baseline.py --output artifacts/frontend-baseline

The probe is intentionally read-only: it does not mutate application state.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from urllib.parse import urljoin

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


VIEWPORT = {"width": 1440, "height": 900}


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


def run(base_url: str, output: Path, timeout_ms: int) -> int:
    output.mkdir(parents=True, exist_ok=True)
    result: dict[str, object] = {
        "base_url": base_url,
        "started_at_epoch": time.time(),
        "checks": {},
        "console_errors": [],
        "page_errors": [],
    }

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(viewport=VIEWPORT)
        page = context.new_page()
        page.on("console", lambda msg: result["console_errors"].append(msg.text) if msg.type == "error" else None)
        page.on("pageerror", lambda exc: result["page_errors"].append(str(exc)))

        started = time.perf_counter()
        try:
            response = page.goto(base_url, wait_until="domcontentloaded", timeout=timeout_ms)
            result["checks"] = {
                "http_status": response.status if response else None,
                "domcontentloaded_ms": round((time.perf_counter() - started) * 1000, 1),
                "title": page.title(),
                "body_text_bytes": len(page.locator("body").inner_text().encode("utf-8")),
            }
            page.screenshot(path=str(output / "desktop.png"), full_page=True)
            result["performance"] = page.evaluate(
                """() => {
                  const n = performance.getEntriesByType('navigation')[0];
                  const paints = Object.fromEntries(performance.getEntriesByType('paint').map(p => [p.name, p.startTime]));
                  return {navigation: n ? {responseStart: n.responseStart, domContentLoaded: n.domContentLoadedEventEnd, loadEventEnd: n.loadEventEnd} : null, paints};
                }"""
            )
            result["checks"]["http_ok"] = bool(response and 200 <= response.status < 400)
        except PlaywrightTimeoutError as exc:
            result["checks"]["navigation_timeout"] = str(exc)
        finally:
            context.close()

        # A useful no-JS fallback check: the document must still be reachable and render HTML.
        nojs = browser.new_context(viewport=VIEWPORT, java_script_enabled=False)
        nojs_page = nojs.new_page()
        try:
            response = nojs_page.goto(base_url, wait_until="domcontentloaded", timeout=timeout_ms)
            text = nojs_page.locator("body").inner_text()
            result["no_js"] = {
                "http_status": response.status if response else None,
                "body_text_bytes": len(text.encode("utf-8")),
                "has_html": bool(nojs_page.locator("html").count()),
                "has_nonempty_body": bool(text.strip()),
            }
            nojs_page.screenshot(path=str(output / "no-js.png"), full_page=True)
        except PlaywrightTimeoutError as exc:
            result["no_js"] = {"error": str(exc)}
        finally:
            nojs.close()

        # Offline behavior is recorded separately and never treated as an application failure.
        offline = browser.new_context(viewport=VIEWPORT)
        offline.set_offline(True)
        offline_page = offline.new_page()
        try:
            offline_page.goto(base_url, wait_until="domcontentloaded", timeout=min(timeout_ms, 5000))
            result["offline"] = {"unexpectedly_loaded": True}
        except Exception as exc:  # noqa: BLE001 - browser engines expose varied network errors.
            result["offline"] = {"request_failed_as_expected": True, "error_type": type(exc).__name__}
        finally:
            offline.close()

        browser.close()

    result["finished_at_epoch"] = time.time()
    write_json(output / "report.json", result)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    errors = result.get("console_errors", []) or []
    page_errors = result.get("page_errors", []) or []
    checks = result.get("checks", {}) or {}
    return 1 if not checks.get("http_ok", False) or errors or page_errors else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.environ.get("BASE_URL"), help="Preview URL")
    parser.add_argument("--output", type=Path, default=Path("artifacts/frontend-baseline"))
    parser.add_argument("--timeout-ms", type=int, default=20_000)
    args = parser.parse_args()
    if not args.base_url:
        parser.error("--base-url or BASE_URL is required")
    return run(args.base_url, args.output, args.timeout_ms)


if __name__ == "__main__":
    raise SystemExit(main())
