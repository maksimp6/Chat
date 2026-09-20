param(
  [Parameter(Mandatory = $false)]
  [string]$BaseUrl = $env:BASE_URL,
  [int]$TimeoutMs = 20000,
  [string]$Output = 'artifacts/frontend-baseline'
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# Interactive fallback: do not make the user discover the required URL syntax.
if ([string]::IsNullOrWhiteSpace($BaseUrl)) {
  $BaseUrl = Read-Host 'Введите полный URL preview, который нужно проверить'
}

if ([string]::IsNullOrWhiteSpace($BaseUrl)) {
  throw 'URL preview не указан. Передайте -BaseUrl или задайте BASE_URL.'
}

try {
  $parsed = [System.Uri]$BaseUrl
  if ($parsed.Scheme -notin @('http', 'https') -or [string]::IsNullOrWhiteSpace($parsed.Host)) {
    throw 'Ожидается полный URL с http:// или https://.'
  }
} catch {
  throw "Некорректный URL preview: $BaseUrl"
}

$python = Get-Command py -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command python -ErrorAction SilentlyContinue }
if (-not $python) { throw 'Python 3 не найден. Сначала запустите tests/setup_frontend_baseline.ps1.' }

Write-Host "Будет проверен URL: $BaseUrl"
Write-Host "Отчёт будет сохранён в: $Output"
$confirmation = Read-Host 'Продолжить? [Y/N]'
if ($confirmation -notmatch '^(?i:y|yes|д|да)$') {
  Write-Host 'Запуск отменён пользователем.'
  exit 2
}

& $python.Source (Join-Path $PSScriptRoot 'frontend_baseline.py') --base-url $BaseUrl --timeout-ms $TimeoutMs --output $Output
exit $LASTEXITCODE
