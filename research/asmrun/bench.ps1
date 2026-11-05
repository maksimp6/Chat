# bench.ps1 — measure asmrun against Ollama (llama.cpp) on the same model and prompts.
#
# Both run greedy (temperature 0) on the raw prompt, 64 new tokens, context 1024,
# so the token streams are comparable. asmrun reads the Ollama GGUF blob directly.
# Each number is the median of -Runs runs; the model is unloaded from the other
# engine before each measurement so neither competes for the four cores.
#
# Usage:  .\bench.ps1 [-Model qwen3:1.7b] [-Runs 3] [-Threads 4]
[CmdletBinding()]
param(
    [string]$Model = "qwen3:1.7b",
    [int]$Runs = 3,
    [int]$Threads = 4,
    [string]$OllamaHost = "http://127.0.0.1:11434"
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$exe = Join-Path $here "build\asmrun.exe"
if (-not (Test-Path $exe)) { throw "build\asmrun.exe not found; run .\build.ps1 first." }

$name, $tag = $Model.Split(":", 2)
if (-not $tag) { $tag = "latest" }
$modelsDir = if ($env:OLLAMA_MODELS) { $env:OLLAMA_MODELS } else { Join-Path $env:USERPROFILE ".ollama\models" }
$manifest = Get-Content (Join-Path $modelsDir "manifests\registry.ollama.ai\library\$name\$tag") -Raw | ConvertFrom-Json
$modelLayer = $manifest.layers | Where-Object { $_.mediaType -eq "application/vnd.ollama.image.model" } | Select-Object -First 1
$blob = Join-Path $modelsDir ("blobs\" + ($modelLayer.digest -replace ":", "-"))
Write-Host "Model blob: $blob"
$weightBytes = (Get-Item $blob).Length

$prompts = @(
    "The capital of France is",
    "Write a short paragraph about why memory bandwidth, not arithmetic, limits the speed of running a language model on a small CPU. Mention caches, quantization and the number of cores, and finish with one practical recommendation for a four-core machine with eight gigabytes of memory."
)

function Median($xs) { $s = @($xs | Sort-Object); return $s[[int]([math]::Floor($s.Count / 2))] }

function Run-Ollama($prompt, $n) {
    $body = @{ model = $Model; prompt = $prompt; raw = $true; stream = $false
        options = @{ temperature = 0; seed = 1; num_predict = $n; num_ctx = 1024 } } | ConvertTo-Json -Depth 4
    $r = Invoke-RestMethod -Uri "$OllamaHost/api/generate" -Method Post -ContentType "application/json" `
        -Body ([Text.Encoding]::UTF8.GetBytes($body)) -TimeoutSec 900
    [pscustomobject]@{
        gen_tok_s    = if ($r.eval_duration) { $r.eval_count / ($r.eval_duration / 1e9) } else { 0 }
        prompt_tok_s = if ($r.prompt_eval_duration) { $r.prompt_eval_count / ($r.prompt_eval_duration / 1e9) } else { 0 }
        text         = $r.response
    }
}

function Run-Asmrun($prompt, $n) {
    $pf = New-TemporaryFile
    [IO.File]::WriteAllText($pf, $prompt, (New-Object Text.UTF8Encoding $false))
    $errf = New-TemporaryFile
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $out = & $exe $blob "-p" $pf "-n" $n "-c" 1024 "-t" $Threads 2> $errf
    $sw.Stop()
    $stats = Get-Content $errf -Raw
    Remove-Item $pf, $errf -ErrorAction SilentlyContinue
    $gen = if ($stats -match "gen_tok_s=([0-9.]+)") { [double]$Matches[1] } else { 0 }
    $pe = if ($stats -match "prompt_tok_s=([0-9.]+)") { [double]$Matches[1] } else { 0 }
    $eff = if ($stats -match "effective_gb_s=([0-9.]+)") { [double]$Matches[1] } else { 0 }
    [pscustomobject]@{ gen_tok_s = $gen; prompt_tok_s = $pe; effective_gb_s = $eff; text = ($out -join "`n") }
}

$rows = @()
for ($p = 0; $p -lt $prompts.Count; $p++) {
    Write-Host "`n=== prompt $p ==="

    & ollama stop $Model 2>$null | Out-Null
    $aRuns = 1..$Runs | ForEach-Object { Run-Asmrun $prompts[$p] 64 }
    $a = $aRuns[-1]

    $warm = Run-Ollama $prompts[$p] 8
    $oRuns = 1..$Runs | ForEach-Object { Run-Ollama $prompts[$p] 64 }
    & ollama stop $Model 2>$null | Out-Null

    $rows += [pscustomobject]@{
        prompt            = $p
        ollama_gen_tok_s  = [math]::Round((Median ($oRuns.gen_tok_s)), 2)
        asmrun_gen_tok_s  = [math]::Round((Median ($aRuns.gen_tok_s)), 2)
        ollama_prompt_tok_s = [math]::Round((Median ($oRuns.prompt_tok_s)), 2)
        asmrun_prompt_tok_s = [math]::Round((Median ($aRuns.prompt_tok_s)), 2)
        asmrun_effective_gb_s = [math]::Round($a.effective_gb_s, 2)
    }
    Write-Host ("ollama : " + ($oRuns[-1].text -replace "\s+", " ").Substring(0, [math]::Min(160, $oRuns[-1].text.Length)))
    Write-Host ("asmrun : " + ($a.text -replace "\s+", " ").Substring(0, [math]::Min(160, $a.text.Length)))
}

Write-Host "`n=== summary (median of $Runs runs, 64 new tokens, greedy) ==="
Write-Host ("model weights = {0:N0} bytes = {1:N3} GB/token" -f $weightBytes, ($weightBytes / 1e9))
$rows | Format-Table -AutoSize
