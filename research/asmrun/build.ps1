# build.ps1 — assemble asmrun with NASM (no linker): the Windows executable and
# the UEFI boot image come from the same sources.
#
#   build\asmrun.exe    Windows console program          (nasm ... asmrun.asm)
#   build\BOOTX64.EFI   UEFI application for a USB stick (nasm -D ASMRUN_EFI ...)
#
# After assembling, the listing is scanned for EVEX-encoded instructions: NASM
# silently picks an AVX-512 encoding for an operand form VEX does not have
# (e.g. a memory source on a vector shift), and the N100 would stop on it with
# an illegal-instruction fault. The UEFI image's PE header is printed for a
# look before it is copied to a stick (make-usb.ps1).
#
# Usage:  .\build.ps1 [-Target win|efi|all] [-Nasm <path to nasm.exe>]
[CmdletBinding()]
param(
    [string]$Nasm = "",
    [ValidateSet("win", "efi", "all")][string]$Target = "all"
)

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

# Lines of a NASM listing whose code bytes start with 0x62 and whose source is a
# vector instruction (mnemonic starting with 'v'): an EVEX prefix, i.e. AVX-512.
function Find-Evex([string]$Listing) {
    Select-String -Path $Listing -Pattern '^\s*\d+\s+[0-9A-F]{8}\s+62[0-9A-F]{2}' |
        Where-Object { $_.Line -match '^\s*\d+\s+[0-9A-F]{8}\s+[0-9A-F-]+\s+v[a-z0-9]+' }
}

function Build-One([string[]]$Defines, [string]$Out, [string]$Listing) {
    $nasmArgs = @("-f", "bin", "-O2", "-I", "inc/", "-I", "src/") + $Defines + @("-l", $Listing, "-o", $Out, "asmrun.asm")
    Write-Host "nasm $($nasmArgs -join ' ')"
    & $nasm @nasmArgs
    if ($LASTEXITCODE -ne 0) { throw "NASM failed with exit code $LASTEXITCODE" }
    $evex = @(Find-Evex $Listing)
    if ($evex.Count -gt 0) {
        $evex | ForEach-Object { Write-Host ("  EVEX: " + $_.Line) }
        throw "$($evex.Count) EVEX-encoded (AVX-512) instruction(s) in $Out; the N100 cannot execute them. Load the memory operand into a register first."
    }
    $fi = Get-Item $Out
    Write-Host ("Built {0} ({1:N0} bytes)" -f $Out, $fi.Length)
}

# A readable dump of the PE32+ header: what the firmware's loader checks.
function Show-PeHeader([string]$Path) {
    $b = [IO.File]::ReadAllBytes($Path)
    $pe = [BitConverter]::ToInt32($b, 0x3C)
    if ($b[$pe] -ne 0x50 -or $b[$pe + 1] -ne 0x45) { throw "${Path}: no PE signature" }
    $coff = $pe + 4
    $machine = [BitConverter]::ToUInt16($b, $coff)
    $nsec = [BitConverter]::ToUInt16($b, $coff + 2)
    $optSize = [BitConverter]::ToUInt16($b, $coff + 16)
    $chars = [BitConverter]::ToUInt16($b, $coff + 18)
    $opt = $coff + 20
    $magic = [BitConverter]::ToUInt16($b, $opt)
    $entry = [BitConverter]::ToUInt32($b, $opt + 16)
    $imageBase = [BitConverter]::ToUInt64($b, $opt + 24)
    $secAlign = [BitConverter]::ToUInt32($b, $opt + 32)
    $fileAlign = [BitConverter]::ToUInt32($b, $opt + 36)
    $sizeOfImage = [BitConverter]::ToUInt32($b, $opt + 56)
    $sizeOfHeaders = [BitConverter]::ToUInt32($b, $opt + 60)
    $subsystem = [BitConverter]::ToUInt16($b, $opt + 68)
    $dllChars = [BitConverter]::ToUInt16($b, $opt + 70)
    $ndirs = [BitConverter]::ToUInt32($b, $opt + 108)
    $dirs = $opt + 112
    $importRva = [BitConverter]::ToUInt32($b, $dirs + 8); $importSize = [BitConverter]::ToUInt32($b, $dirs + 12)
    $relocRva = [BitConverter]::ToUInt32($b, $dirs + 40); $relocSize = [BitConverter]::ToUInt32($b, $dirs + 44)
    $subsystemName = switch ($subsystem) { 3 { "WINDOWS_CUI" } 10 { "EFI_APPLICATION" } default { "$subsystem" } }
    Write-Host ("  PE32+ magic=0x{0:X4} machine=0x{1:X4} sections={2} characteristics=0x{3:X4} subsystem={4} dllcharacteristics=0x{5:X4}" -f $magic, $machine, $nsec, $chars, $subsystemName, $dllChars)
    Write-Host ("  entry=0x{0:X} imagebase=0x{1:X} sectionalign=0x{2:X} filealign=0x{3:X} sizeofimage=0x{4:X} sizeofheaders=0x{5:X} file={6:N0} bytes" -f $entry, $imageBase, $secAlign, $fileAlign, $sizeOfImage, $sizeOfHeaders, $b.Length)
    Write-Host ("  import dir rva=0x{0:X} size={1}; basereloc dir rva=0x{2:X} size={3}" -f $importRva, $importSize, $relocRva, $relocSize)
    if ($relocSize -gt 0) {
        $blockVa = [BitConverter]::ToUInt32($b, $relocRva); $blockSize = [BitConverter]::ToUInt32($b, $relocRva + 4)
        $entry0 = [BitConverter]::ToUInt16($b, $relocRva + 8)
        Write-Host ("  reloc block: VirtualAddress=0x{0:X} SizeOfBlock={1} entry0=0x{2:X4} (type {3})" -f $blockVa, $blockSize, $entry0, ($entry0 -shr 12))
    }
    $sec = $opt + $optSize
    for ($i = 0; $i -lt $nsec; $i++) {
        $s = $sec + 40 * $i
        $name = [Text.Encoding]::ASCII.GetString($b, $s, 8).TrimEnd([char]0)
        $vsize = [BitConverter]::ToUInt32($b, $s + 8); $va = [BitConverter]::ToUInt32($b, $s + 12)
        $rsize = [BitConverter]::ToUInt32($b, $s + 16); $raw = [BitConverter]::ToUInt32($b, $s + 20)
        $sc = [BitConverter]::ToUInt32($b, $s + 36)
        Write-Host ("  section {0,-6} va=0x{1:X} vsize=0x{2:X} raw=0x{3:X} rawsize=0x{4:X} characteristics=0x{5:X8}" -f $name, $va, $vsize, $raw, $rsize, $sc)
        if ($raw + $rsize -gt $b.Length) { throw "section $name runs past the end of the file" }
        if ($va -ne $raw) { throw "section ${name}: RVA 0x$($va.ToString('X')) != file offset 0x$($raw.ToString('X')) (pe.inc relies on them being equal)" }
    }
    if ($sizeOfImage -ne $b.Length) { throw "SizeOfImage 0x$($sizeOfImage.ToString('X')) != file length 0x$($b.Length.ToString('X'))" }
}

$nasm = Find-Nasm $Nasm
Write-Host "NASM: $nasm"
New-Item -ItemType Directory -Force -Path (Join-Path $here "build") | Out-Null

if ($Target -eq "win" -or $Target -eq "all") {
    $out = Join-Path $here "build\asmrun.exe"
    Build-One @() $out (Join-Path $here "build\asmrun.lst")
    Show-PeHeader $out
    Write-Host "Run:  .\build\asmrun.exe <model.gguf> -p prompt.txt -n 64"
}
if ($Target -eq "efi" -or $Target -eq "all") {
    $out = Join-Path $here "build\BOOTX64.EFI"
    Build-One @("-D", "ASMRUN_EFI") $out (Join-Path $here "build\BOOTX64.lst")
    Show-PeHeader $out
    Write-Host "Stick: .\make-usb.ps1 -Drive E:   (copies BOOTX64.EFI, the model and asmrun.cfg)"
}
