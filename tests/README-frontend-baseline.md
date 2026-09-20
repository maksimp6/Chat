# Frontend baseline on Windows

## Supported setup

- Windows 10/11
- Python 3.11 or newer
- PowerShell 5.1+ or PowerShell 7
- Network access to the preview URL

Run PowerShell from the repository root. If script execution is restricted, use:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

## First run

```powershell
.\tests\setup_frontend_baseline.ps1
.\tests\run_frontend_baseline.ps1
```

The runner will **ask for the preview URL** if `-BaseUrl` and `BASE_URL` are not set. It will then display the target URL and output directory and ask for confirmation before starting.

You can also provide the URL explicitly:

```powershell
.\tests\run_frontend_baseline.ps1 -BaseUrl 'https://your-preview-url/'
```

For the planned severe network baseline—10 KiB/s download, **0.065 KiB/s upload**, and 2500 ms additional latency—run:

```powershell
.\tests\run_frontend_baseline.ps1 -BaseUrl 'http://88.218.66.166/preview/pr-228/' -DownloadKbps 10 -UploadKbps 0.065 -LatencyMs 2500 -TimeoutMs 180000 -Trace
```

Or use the Python probe directly:

```powershell
py tests/frontend_baseline.py --base-url 'http://88.218.66.166/preview/pr-228/' --download-kbps 10 --upload-kbps 0.065 --latency-ms 2500 --timeout-ms 180000 --trace
```

The upload value is `0.26 / 2 / 2 = 0.065 KiB/s`. Throughput arguments are **KiB/s**, not decimal KB/s.

The probe first waits for navigation `commit`, then samples the shell at approximately 250 ms, 1 s, 3 s, and 5 s. This is required to catch early blank-screen failures that would be missed by waiting for `DOMContentLoaded` first.

The report is written to `artifacts/frontend-baseline/report.json`. Screenshots are written beside it. With `-Trace`/ `--trace`, a Playwright trace is also written as `artifacts/frontend-baseline/trace.zip`.

## Common Windows issues

- **`py` or `python` not found:** install Python 3.11+ and enable the Python launcher/PATH option.
- **`playwright` is not recognized:** run the setup script again; invoke it through Python, not as a standalone executable.
- **Chromium executable missing:** run `py -m playwright install chromium`.
- **PowerShell execution policy:** use the process-scoped command above; do not change the machine policy.
- **TLS/certificate errors:** verify the preview URL in the browser first. Do not disable certificate validation in the probe.
- **Proxy/VPN interference:** test the same URL in the browser and record the proxy/VPN state with the report.
- **Non-ASCII paths:** run from the repository root and keep the output path relative, or pass an absolute path.
- **Port or timeout problems:** increase `-TimeoutMs`, for example `-TimeoutMs 60000`.
- **False-looking console errors:** inspect `report.json`; the probe records browser errors verbatim and intentionally does not hide them.

## Exit codes

- `0`: HTTP navigation succeeded and no page/console errors were recorded.
- `1`: a navigation, browser, page, or console error was recorded.
- `2`: the user cancelled the run.

A failed run still produces `report.json` whenever the browser can be started. Attach the whole `artifacts/frontend-baseline` directory when reporting a failure.

## Interpreting severe-network failures

If the report contains `blank_dom_observed: true`, this means that an early sampled milestone had no visible `#app-root` and zero body text bytes. It does **not** prove that the server failed to deliver HTML.

For a blank-screen result, inspect the early milestones and rerun with `--trace`/ `-Trace`. The root cause must be separated into transport, parsing, CSS/visibility, inline bootstrap, deferred-script execution, or application initialization before application code is changed.

The manual observation already recorded in Issue #227 used a different profile: 10 KB/s download, 10 KB/s upload, 200 ms latency. Do not report it as a measurement of the planned 10 KiB/s / 0.065 KiB/s / 2500 ms profile.

## Scope limits

This probe does not claim to complete Android WebView validation, API fault injection, stale-cache validation, or Lighthouse analysis. Those remain separate evidence items for Issue #227. The automated runner also does not replace true device-level network testing.
