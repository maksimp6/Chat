param(
  [Parameter(Mandatory = $false)]
  [string]$BaseUrl = $env:BASE_URL,
  [int]$TimeoutMs = 20000,
  [string]$Output = 'artifacts/frontend-baseline'
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if ([string]::IsNullOrWhiteSpace($BaseUrl)) {
  throw 'Provide -BaseUrl or set BASE_URL to the preview URL.'
}

$python = Get-Command py -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command python -ErrorAction SilentlyContinue }
if (-not $python) { throw 'Python 3 was not found. Run tests/setup_frontend_baseline.ps1 first.' }

& $python.Source (Join-Path $PSScriptRoot 'frontend_baseline.py') --base-url $BaseUrl --timeout-ms $TimeoutMs --output $Output
exit $LASTEXITCODE
