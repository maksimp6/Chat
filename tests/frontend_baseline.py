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
BYTES_PER_KIB = 1024
EARLY_MILESTONES_MS = (250, 1000, 3000, 5000)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False),
        encoding="utf-8",
        newline="\n",
    )


def valid_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def shell_state(page) -> dict[str, object]:
    return page.evaluate(
        """() => {
          const body = document.body;
          const root = document.querySelector("#app-root");
          const input = document.querySelector("#msg-input");
          const rect = root ? root.getBoundingClientRect() : null;
          const style = root ? getComputedStyle(root) : null;
          const rootVisible = Boolean(
            root &&
            rect &&
            rect.width > 0 &&
            rect.height > 0 &&
            style &&
            style.display !== "none" &&
            style.visibility !== "hidden"
          );
          const inputVisible = Boolean(
            input &&
            input.getBoundingClientRect().width > 0 &&
            input.getBoundingClientRect().height > 0 &&
            getComputedStyle(input).display !== "none" &&
            getComputedStyle(input).visibility !== "hidden"
          );
          const bodyText = body ? (body.innerText || "").trim() : "";
          return {
            ready_state: document.readyState,
            html_present: Boolean(document.documentElement),
            body_present: Boolean(body),
            body_child_count: body ? body.children.length : 0,
            body_text_bytes: new TextEncoder().encode(bodyText).length,
            app_root_present: Boolean(root),
            app_root_visible: rootVisible,
            message_input_present: Boolean(input),
            message_input_visible: inputVisible,
            first_usable_candidate: rootVisible && inputVisible
          };
        }"""
    )


def run(
    base_url: str,
    output: Path,
    timeout_ms: int,
    download_kib_per_second: float | None = None,
    upload_kib_per_second: float | None = None,
    latency_ms: int = 0,
    capture_trace: bool = False,
) -> int:
    output.mkdir(parents=True, exist_ok=True)
    network_enabled = (
        download_kib_per_second is not None
        or upload_kib_per_second is not None
        or latency_ms > 0
    )
    download_bps = (
        round(download_kib_per_second * BYTES_PER_KIB)
        if download_kib_per_second is not None
        else None
    )
    upload_bps = (
        round(upload_kib_per_second * BYTES_PER_KIB)
        if upload_kib_per_second is not None
        else None
    )

    result: dict[str, object] = {
        "schema_version": 2,
        "base_url": base_url,
        "platform": sys.platform,
        "python": sys.version,
        "started_at_epoch": time.time(),
        "checks": {},
        "console_errors": [],
        "page_errors": [],
        "failed_requests": [],
        "http_error_responses": [],
        "warnings": [],
        "network_emulation": {
            "enabled": network_enabled,
            "download_kib_per_second": download_kib_per_second,
            "upload_kib_per_second": upload_kib_per_second,
            "download_bytes_per_second": download_bps,
            "upload_bytes_per_second": upload_bps,
            "latency_ms": latency_ms,
        },
        "early_milestones": [],
    }

    def record_error(kind: str, value: object) -> None:
        result.setdefault(kind, []).append(str(value))  # type: ignore[union-attr]

    def record_response(response) -> None:
        try:
            status = response.status
            if status >= 400:
                result["http_error_responses"].append(
                    {"url": response.url, "status": status}
                )
        except Exception:
            return

    def record_request_failed(request) -> None:
        try:
            result["failed_requests"].append(
                {"url": request.url, "failure": request.failure}
            )
        except Exception:
            return

    with sync_playwright() as pw:
        browser = None
        try:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)

            if capture_trace:
                context.tracing.start(
                    screenshots=True,
                    snapshots=True,
                    sources=True,
                )

            page = context.new_page()
            page.on(
                "console",
                lambda msg: record_error("console_errors", msg.text)
                if msg.type == "error"
                else None,
            )
            page.on("pageerror", lambda exc: record_error("page_errors", exc))
            page.on("response", record_response)
            page.on("requestfailed", record_request_failed)

            if network_enabled:
                cdp = context.new_cdp_session(page)
                cdp.send("Network.enable")
                cdp.send(
                    "Network.emulateNetworkConditions",
                    {
                        "offline": False,
                        "latency": latency_ms,
                        "downloadThroughput": download_bps if download_bps is not None else -1,
                        "uploadThroughput": upload_bps if upload_bps is not None else -1,
                        "connectionType": "cellular2g",
                    },
                )

            started = time.perf_counter()
            main_response = None
            try:
                main_response = page.goto(
                    base_url,
                    wait_until="commit",
                    timeout=timeout_ms,
                )
                commit_ms = round((time.perf_counter() - started) * 1000, 1)
                result["checks"]["commit_ms"] = commit_ms
                result["checks"]["main_response_status"] = (
                    main_response.status if main_response else None
                )

                for milestone_ms in EARLY_MILESTONES_MS:
                    target = started + milestone_ms / 1000
                    remaining = target - time.perf_counter()
                    if remaining > 0:
                        page.wait_for_timeout(round(remaining * 1000))
                    try:
                        state = shell_state(page)
                        result["early_milestones"].append(
                            {
                                "elapsed_ms": round(
                                    (time.perf_counter() - started) * 1000, 1
                                ),
                                "target_ms": milestone_ms,
                                **state,
                            }
                        )
                    except Exception as exc:  # noqa: BLE001
                        result["early_milestones"].append(
                            {
                                "elapsed_ms": round(
                                    (time.perf_counter() - started) * 1000, 1
                                ),
                                "target_ms": milestone_ms,
                                "error": f"{type(exc).__name__}: {exc}",
                            }
                        )

                remaining_timeout = max(
                    1000,
                    timeout_ms - int((time.perf_counter() - started) * 1000),
                )
                try:
                    page.wait_for_load_state(
                        "domcontentloaded",
                        timeout=remaining_timeout,
                    )
                except PlaywrightTimeoutError as exc:
                    result["checks"]["navigation_timeout"] = str(exc)

                checks = result["checks"]
                checks.update(
                    {
                        "http_status": main_response.status if main_response else None,
                        "domcontentloaded_ms": round(
                            (time.perf_counter() - started) * 1000, 1
                        ),
                        "title": page.title(),
                        "body_text_bytes": len(
                            page.locator("body").inner_text().encode("utf-8")
                        ),
                        "http_ok": bool(
                            main_response
                            and 200 <= main_response.status < 400
                        ),
                        "resource_count": page.evaluate(
                            "() => performance.getEntriesByType('resource').length"
                        ),
                        "resource_transfer_bytes": page.evaluate(
                            "() => performance.getEntriesByType('resource').reduce((sum, e) => sum + (e.transferSize || 0), 0)"
                        ),
                    }
                )
                checks["blank_dom_observed"] = any(
                    not milestone.get("app_root_visible", False)
                    and milestone.get("body_text_bytes", 0) == 0
                    for milestone in result["early_milestones"]
                )
                checks["first_usable_milestone_ms"] = next(
                    (
                        milestone["elapsed_ms"]
                        for milestone in result["early_milestones"]
                        if milestone.get("first_usable_candidate")
                    ),
                    None,
                )

                page.screenshot(
                    path=str(output / "desktop.png"),
                    full_page=True,
                )
                result["performance"] = page.evaluate(
                    """() => {
                      const n = performance.getEntriesByType("navigation")[0];
                      const paints = Object.fromEntries(
                        performance.getEntriesByType("paint").map(
                          p => [p.name, p.startTime]
                        )
                      );
                      return {
                        navigation: n ? {
                          startTime: n.startTime,
                          responseStart: n.responseStart,
                          domContentLoaded: n.domContentLoadedEventEnd,
                          loadEventEnd: n.loadEventEnd
                        } : null,
                        paints
                      };
                    }"""
                )
            except PlaywrightTimeoutError as exc:
                result["checks"]["navigation_timeout"] = str(exc)
            except Exception as exc:  # noqa: BLE001
                result["checks"]["navigation_error"] = f"{type(exc).__name__}: {exc}"
            finally:
                if capture_trace:
                    try:
                        context.tracing.stop(path=str(output / "trace.zip"))
                    except Exception as exc:  # noqa: BLE001
                        result["warnings"].append(
                            f"trace_stop_failed: {type(exc).__name__}: {exc}"
                        )
                context.close()

            nojs = browser.new_context(viewport=VIEWPORT, java_script_enabled=False)
            nojs_page = nojs.new_page()
            try:
                response = nojs_page.goto(
                    base_url,
                    wait_until="domcontentloaded",
                    timeout=timeout_ms,
                )
                text_content = nojs_page.locator("body").inner_text()
                result["no_js"] = {
                    "http_status": response.status if response else None,
                    "body_text_bytes": len(text_content.encode("utf-8")),
                    "has_html": bool(nojs_page.locator("html").count()),
                    "has_nonempty_body": bool(text_content.strip()),
                    "app_root_present": bool(nojs_page.locator("#app-root").count()),
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
                offline_page.goto(
                    base_url,
                    wait_until="domcontentloaded",
                    timeout=min(timeout_ms, 5000),
                )
                result["offline"] = {"unexpectedly_loaded": True}
            except Exception as exc:  # noqa: BLE001
                result["offline"] = {
                    "request_failed_as_expected": True,
                    "error_type": type(exc).__name__,
                }
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
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/frontend-baseline"),
    )
    parser.add_argument("--timeout-ms", type=int, default=20_000)
    parser.add_argument(
        "--download-kbps",
        type=float,
        default=None,
        help="Emulate download throughput in KiB/s.",
    )
    parser.add_argument(
        "--upload-kbps",
        type=float,
        default=None,
        help="Emulate upload throughput in KiB/s.",
    )
    parser.add_argument(
        "--latency-ms",
        type=int,
        default=0,
        help="Additional network latency in milliseconds.",
    )
    parser.add_argument(
        "--trace",
        action="store_true",
        help="Capture a Playwright tracing archive as trace.zip.",
    )
    args = parser.parse_args()

    if not args.base_url:
        parser.error("--base-url or BASE_URL is required")
    if not valid_url(args.base_url):
        parser.error("--base-url must be an absolute http(s) URL")
    if args.timeout_ms < 1000:
        parser.error("--timeout-ms must be at least 1000")
    if args.download_kbps is not None and args.download_kbps <= 0:
        parser.error("--download-kbps must be greater than 0")
    if args.upload_kbps is not None and args.upload_kbps <= 0:
        parser.error("--upload-kbps must be greater than 0")
    if args.latency_ms < 0:
        parser.error("--latency-ms must be at least 0")

    return run(
        args.base_url.rstrip("/") + "/",
        args.output,
        args.timeout_ms,
        download_kib_per_second=args.download_kbps,
        upload_kib_per_second=args.upload_kbps,
        latency_ms=args.latency_ms,
        capture_trace=args.trace,
    )


if __name__ == "__main__":
    raise SystemExit(main())
