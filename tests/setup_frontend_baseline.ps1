$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

Write-Host '== Frontend baseline setup ==' -ForegroundColor Cyan

$python = Get-Command py -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command python -ErrorAction SilentlyContinue }
if (-not $python) {
  throw 'Python 3 was not found. Install Python 3.11+ and enable the Python launcher or PATH entry.'
}

& $python.Source -m pip install --upgrade pip
& $python.Source -m pip install -r (Join-Path $PSScriptRoot 'requirements-frontend.txt')
& $python.Source -m playwright install chromium

Write-Host ''
Write-Host 'Setup complete.' -ForegroundColor Green
Write-Host 'Run:'
Write-Host '  $env:BASE_URL = "https://your-preview-url/"'
Write-Host '  py tests/frontend_baseline.py --output artifacts/frontend-baseline'
