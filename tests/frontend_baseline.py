"""Cross-platform automated frontend baseline probe.

Windows PowerShell:
  py -m pip install -r tests/requirements-frontend.txt
  py -m playwright install chromium
  py tests/frontend_baseline.py --base-url https://example.test/preview/pr-228/ --output artifacts/frontend-baseline

The probe is read-only. Individual checks are recorded even when another check fails.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

VIEWPORT = {"width": 1440, "height": 900}


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")


def valid_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def run(base_url: str, output: Path, timeout_ms: int) -> int:
    output.mkdir(parents=True, exist_ok=True)
    result: dict[str, object] = {
        "schema_version": 1,
        "base_url": base_url,
        "platform": sys.platform,
        "python": sys.version,
        "started_at_epoch": time.time(),
        "checks": {},
        "console_errors": [],
        "page_errors": [],
        "warnings": [],
    }

    def record_error(kind: str, value: object) -> None:
        result.setdefault(kind, []).append(str(value))  # type: ignore[union-attr]

    with sync_playwright() as pw:
        browser = None
        try:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            page.on("console", lambda msg: record_error("console_errors", msg.text) if msg.type == "error" else None)
            page.on("pageerror", lambda exc: record_error("page_errors", exc))
            started = time.perf_counter()
            try:
                response = page.goto(base_url, wait_until="domcontentloaded", timeout=timeout_ms)
                checks = result["checks"]
                checks.update({
                    "http_status": response.status if response else None,
                    "domcontentloaded_ms": round((time.perf_counter() - started) * 1000, 1),
                    "title": page.title(),
                    "body_text_bytes": len(page.locator("body").inner_text().encode("utf-8")),
                    "http_ok": bool(response and 200 <= response.status < 400),
                })
                page.screenshot(path=str(output / "desktop.png"), full_page=True)
                result["performance"] = page.evaluate("""() => {
                  const n = performance.getEntriesByType('navigation')[0];
                  const paints = Object.fromEntries(performance.getEntriesByType('paint').map(p => [p.name, p.startTime]));
                  return {navigation: n ? {responseStart: n.responseStart, domContentLoaded: n.domContentLoadedEventEnd, loadEventEnd: n.loadEventEnd} : null, paints};
                }""")
            except PlaywrightTimeoutError as exc:
                result["checks"]["navigation_timeout"] = str(exc)
            except Exception as exc:  # noqa: BLE001 - preserve diagnostics for browser-specific failures.
                result["checks"]["navigation_error"] = f"{type(exc).__name__}: {exc}"
            finally:
                context.close()

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
            except Exception as exc:  # noqa: BLE001
                result["no_js"] = {"error": f"{type(exc).__name__}: {exc}"}
            finally:
                nojs.close()

            offline = browser.new_context(viewport=VIEWPORT)
            offline.set_offline(True)
            offline_page = offline.new_page()
            try:
                offline_page.goto(base_url, wait_until="domcontentloaded", timeout=min(timeout_ms, 5000))
                result["offline"] = {"unexpectedly_loaded": True}
            except Exception as exc:  # noqa: BLE001 - engine-specific network errors are expected.
                result["offline"] = {"request_failed_as_expected": True, "error_type": type(exc).__name__}
            finally:
                offline.close()
        except Exception as exc:  # noqa: BLE001
            result["fatal_error"] = f"{type(exc).__name__}: {exc}"
        finally:
            if browser is not None:
                browser.close()

    result["finished_at_epoch"] = time.time()
    write_json(output / "report.json", result)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    checks = result.get("checks", {})
    errors = result.get("console_errors", []) or []
    page_errors = result.get("page_errors", []) or []
    fatal = result.get("fatal_error")
    return 1 if fatal or not checks.get("http_ok", False) or errors or page_errors else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.environ.get("BASE_URL"), help="Preview URL")
    parser.add_argument("--output", type=Path, default=Path("artifacts/frontend-baseline"))
    parser.add_argument("--timeout-ms", type=int, default=20_000)
    args = parser.parse_args()
    if not args.base_url:
        parser.error("--base-url or BASE_URL is required")
    if not valid_url(args.base_url):
        parser.error("--base-url must be an absolute http(s) URL")
    if args.timeout_ms < 1000:
        parser.error("--timeout-ms must be at least 1000")
    return run(args.base_url.rstrip("/") + "/", args.output, args.timeout_ms)


if __name__ == "__main__":
    raise SystemExit(main())
