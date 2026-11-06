# make-usb.ps1 — put asmrun and the model on a USB stick that boots with no
# operating system.
#
# The stick ends up with:
#   \EFI\BOOT\BOOTX64.EFI   the runner (build\BOOTX64.EFI from .\build.ps1)
#   \model.gguf             the Ollama model blob (qwen3:1.7b by default, 1.36 GB)
#   \asmrun.cfg             the arguments, one line, e.g. "model.gguf -i"
#
# The firmware needs FAT32 (not exFAT or NTFS) and Secure Boot off: the image
# is not signed. Formatting is only done when -Format is given, after a
# confirmation; otherwise the files are copied onto what is there.
#
# Usage:  .\make-usb.ps1 -Drive E: [-Model qwen3:1.7b] [-Args "model.gguf -i"] [-Format]
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Drive,
    [string]$Model = "qwen3:1.7b",
    [string]$Args = "model.gguf -i",
    [switch]$Format
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$efi = Join-Path $here "build\BOOTX64.EFI"
if (-not (Test-Path $efi)) { throw "build\BOOTX64.EFI not found; run .\build.ps1 -Target efi first." }

$letter = $Drive.TrimEnd(':', '\').ToUpper()
$vol = Get-Volume -DriveLetter $letter -ErrorAction SilentlyContinue
if (-not $vol) { throw "no volume ${letter}:" }
$part = Get-Partition -DriveLetter $letter -ErrorAction SilentlyContinue
$disk = if ($part) { Get-Disk -Number $part.DiskNumber } else { $null }
if ($disk) {
    Write-Host ("Target: {0}: on disk {1} '{2}' ({3}, {4:N1} GB)" -f $letter, $disk.Number, $disk.FriendlyName, $disk.BusType, ($disk.Size / 1GB))
    if ($disk.BusType -ne "USB") { throw "${letter}: is not on a USB disk (bus $($disk.BusType)); refusing." }
}

# The model blob, from the Ollama manifest (same lookup as bench.ps1).
$name, $tag = $Model.Split(":", 2)
if (-not $tag) { $tag = "latest" }
$modelsDir = if ($env:OLLAMA_MODELS) { $env:OLLAMA_MODELS } else { Join-Path $env:USERPROFILE ".ollama\models" }
$manifestPath = Join-Path $modelsDir "manifests\registry.ollama.ai\library\$name\$tag"
if (-not (Test-Path $manifestPath)) { throw "Ollama manifest not found: $manifestPath (is $Model pulled?)" }
$manifest = Get-Content $manifestPath -Raw | ConvertFrom-Json
$modelLayer = $manifest.layers | Where-Object { $_.mediaType -eq "application/vnd.ollama.image.model" } | Select-Object -First 1
$blob = Join-Path $modelsDir ("blobs\" + ($modelLayer.digest -replace ":", "-"))
$blobSize = (Get-Item $blob).Length
Write-Host ("Model:  {0} ({1:N0} bytes)" -f $blob, $blobSize)

if ($Format) {
    Write-Host ""
    Write-Host "About to FORMAT ${letter}: as FAT32. Everything on it will be erased." -ForegroundColor Yellow
    $answer = Read-Host "Type the drive letter ($letter) to confirm"
    if ($answer.ToUpper() -ne $letter) { throw "not confirmed; nothing done." }
    # Format-Volume makes FAT32 above 32 GB (a 117 GB stick worked); only format.exe and the Explorer dialog stop at 32 GB.
    Format-Volume -DriveLetter $letter -FileSystem FAT32 -NewFileSystemLabel "ASMRUN" -Confirm:$false | Out-Null
    $vol = Get-Volume -DriveLetter $letter
}

if ($vol.FileSystem -ne "FAT32") {
    throw "${letter}: is $($vol.FileSystem); the firmware reads only FAT32. Re-run with -Format (erases the stick)."
}

$root = "${letter}:\"
$dstModel = Join-Path $root "model.gguf"
$need = $blobSize + 1MB
if ((Test-Path $dstModel) -and ((Get-Item $dstModel).Length -eq $blobSize)) {
    Write-Host "model.gguf is already on the stick with the right size; not copied again."
} else {
    if ($vol.SizeRemaining -lt $need) { throw ("not enough free space on ${letter}: ({0:N0} bytes free, {1:N0} needed)" -f $vol.SizeRemaining, $need) }
    Write-Host "Copying the model (this takes a few minutes on a USB stick)..."
    $sw = [Diagnostics.Stopwatch]::StartNew()
    Copy-Item -Path $blob -Destination $dstModel -Force
    $sw.Stop()
    Write-Host ("  {0:N0} bytes in {1:N0} s ({2:N1} MB/s)" -f $blobSize, $sw.Elapsed.TotalSeconds, ($blobSize / 1MB / [math]::Max($sw.Elapsed.TotalSeconds, 0.001)))
}

$bootDir = Join-Path $root "EFI\BOOT"
New-Item -ItemType Directory -Force -Path $bootDir | Out-Null
Copy-Item -Path $efi -Destination (Join-Path $bootDir "BOOTX64.EFI") -Force
Write-Host "Copied BOOTX64.EFI to \EFI\BOOT\."

# ASCII without a BOM: the runner reads the bytes as they are.
[IO.File]::WriteAllText((Join-Path $root "asmrun.cfg"), $Args + "`r`n", (New-Object Text.ASCIIEncoding))
Write-Host "Wrote asmrun.cfg: $Args"

Write-Host ""
Write-Host "Done. To run it:"
Write-Host "  1. Enter the firmware setup at power-on (Del, Esc or F2 on this AMI firmware) and turn Secure Boot off;"
Write-Host "     the image is unsigned and the firmware refuses unsigned boot files while Secure Boot is on."
Write-Host "  2. Boot from the stick (the boot-device menu, F7/F11/Esc at power-on, or put the USB first in the boot order)."
Write-Host "  3. The screen shows the stage lines, the model loads from the stick, then a '>' prompt:"
Write-Host "     type a prompt and press Enter; an empty line or Esc ends the session and returns to the firmware."
Write-Host "  asmrun.cfg takes the Windows command-line arguments: e.g. 'model.gguf -p prompt.txt -n 64' for one run,"
Write-Host "  'model.gguf --bw' for the memory bandwidth probe, 'model.gguf --kernels' for the kernel benchmark."
