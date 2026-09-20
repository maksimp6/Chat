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
.\tests\run_frontend_baseline.ps1 -BaseUrl 'https://your-preview-url/'
```

The report is written to `artifacts/frontend-baseline/report.json`. Screenshots are written beside it.

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

A failed run still produces `report.json` whenever the browser can be started. Attach the whole `artifacts/frontend-baseline` directory when reporting a failure.

## Scope limits

This probe does not claim to complete Android WebView validation, true device-level slow-3G testing, API fault injection, or Lighthouse analysis. Those remain separate evidence items for Issue #227.
