# build.ps1 — assemble asmrun.exe with NASM (no linker).
#
# asmrun is one `nasm -f bin` translation unit that emits a complete PE
# executable. This must run on a machine where NASM can run; on a machine with
# Smart App Control enforcing (VerifiedAndReputablePolicyState=1) both NASM and
# the produced asmrun.exe are blocked from running, and this script will report
# that NASM could not start.
#
# Usage:  .\build.ps1 [-Nasm <path to nasm.exe>]
[CmdletBinding()]
param([string]$Nasm = "")

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

function Find-Nasm {
    param([string]$Explicit)
    if ($Explicit) { return $Explicit }
    $cmd = Get-Command nasm -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($p in @(
            "C:\Program Files\NASM\nasm.exe",
            "C:\Program Files (x86)\NASM\nasm.exe",
            "$env:LOCALAPPDATA\bin\NASM\nasm.exe",
            "$env:USERPROFILE\tools\nasm\nasm-3.02\nasm.exe")) {
        if (Test-Path $p) { return $p }
    }
    throw "nasm.exe not found. Install NASM (https://www.nasm.us) or pass -Nasm <path>."
}

$nasm = Find-Nasm $Nasm
Write-Host "NASM: $nasm"

New-Item -ItemType Directory -Force -Path (Join-Path $here "build") | Out-Null
$out = Join-Path $here "build\asmrun.exe"

$args = @("-f", "bin", "-O2", "-I", "inc/", "-I", "src/", "-o", $out, "asmrun.asm")
Write-Host "nasm $($args -join ' ')"
& $nasm @args
if ($LASTEXITCODE -ne 0) { throw "NASM failed with exit code $LASTEXITCODE" }

$fi = Get-Item $out
Write-Host ("Built {0} ({1:N0} bytes)" -f $out, $fi.Length)
Write-Host "Run:  .\build\asmrun.exe <model.gguf> -p prompt.txt -n 64"
