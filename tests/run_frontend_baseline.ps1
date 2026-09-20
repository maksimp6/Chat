param(
  [Parameter(Mandatory = $false)]
  [string]$BaseUrl = $env:BASE_URL,
  [int]$TimeoutMs = 20000,
  [double]$DownloadKbps,
  [double]$UploadKbps,
  [int]$LatencyMs = 0,
  [switch]$Trace,
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

if ($TimeoutMs -lt 1000) { throw '-TimeoutMs должен быть не меньше 1000.' }
if ($DownloadKbps -and $DownloadKbps -le 0) { throw '-DownloadKbps должен быть больше 0.' }
if ($UploadKbps -and $UploadKbps -le 0) { throw '-UploadKbps должен быть больше 0.' }
if ($LatencyMs -lt 0) { throw '-LatencyMs не может быть отрицательным.' }

$python = Get-Command py -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command python -ErrorAction SilentlyContinue }
if (-not $python) { throw 'Python 3 не найден. Сначала запустите tests/setup_frontend_baseline.ps1.' }

Write-Host "Будет проверен URL: $BaseUrl"
Write-Host "Отчёт будет сохранён в: $Output"
if ($DownloadKbps -or $UploadKbps -or $LatencyMs -gt 0) {
  Write-Host "Сетевой профиль: download=$DownloadKbps KiB/s, upload=$UploadKbps KiB/s, latency=$LatencyMs ms"
}
if ($Trace) { Write-Host 'Performance trace: включён' }
$confirmation = Read-Host 'Продолжить? [Y/N]'
if ($confirmation -notmatch '^(?i:y|yes|д|да)$') {
  Write-Host 'Запуск отменён пользователем.'
  exit 2
}

$arguments = @(
  (Join-Path $PSScriptRoot 'frontend_baseline.py'),
  '--base-url', $BaseUrl,
  '--timeout-ms', $TimeoutMs,
  '--output', $Output
)
if ($PSBoundParameters.ContainsKey('DownloadKbps')) {
  $arguments += @('--download-kbps', $DownloadKbps.ToString([Globalization.CultureInfo]::InvariantCulture))
}
if ($PSBoundParameters.ContainsKey('UploadKbps')) {
  $arguments += @('--upload-kbps', $UploadKbps.ToString([Globalization.CultureInfo]::InvariantCulture))
}
if ($LatencyMs -gt 0) { $arguments += @('--latency-ms', $LatencyMs) }
if ($Trace) { $arguments += '--trace' }

& $python.Source @arguments
exit $LASTEXITCODE
