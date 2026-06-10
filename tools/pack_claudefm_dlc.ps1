<#
  pack_claudefm_dlc.ps1 - Pack the CLAUDE FM station DLC folder into dlc.rpf, headlessly, with
  CodeWalker.Core. OPEN encryption (the game reads OPEN dlc.rpf from the mods folder via OpenIV.asi;
  headless CodeWalker cannot write NG - see DISCOVERIES.md sec 3).

  Build happens in %TEMP% to avoid Dropbox locking the .rpf mid-write, then is copied to dist/.

  Usage:
    powershell -ExecutionPolicy Bypass -File tools\pack_claudefm_dlc.ps1 `
        -CwDir "C:\path\to\CodeWalker30_dev48" `
        [-Stage tools\claudefm_dlc] [-Out dist\claudefm\dlc.rpf]
#>
param(
    [string]$CwDir = $env:CODEWALKER_DIR,
    [string]$Stage = (Join-Path $PSScriptRoot "claudefm_dlc"),
    [string]$Out   = (Join-Path (Split-Path $PSScriptRoot -Parent) "dist\claudefm\dlc.rpf")
)
$ErrorActionPreference = "Stop"
if (-not $CwDir) { throw "Set -CwDir or `$env:CODEWALKER_DIR to your CodeWalker folder" }

Get-ChildItem -LiteralPath $CwDir -Include *.dll,*.exe -Recurse | Unblock-File -ErrorAction SilentlyContinue
$asm = [System.Reflection.Assembly]::LoadFrom((Join-Path $CwDir "CodeWalker.Core.dll"))

$build = Join-Path $env:TEMP "claudefm_pack"      # NOT inside Dropbox
New-Item -ItemType Directory -Force $build | Out-Null
$rpfPath = Join-Path $build "dlc.rpf"
if (Test-Path $rpfPath) { [System.IO.File]::Delete($rpfPath) }

$rpf = [CodeWalker.GameFiles.RpfFile]::CreateNew($build, "dlc.rpf", [CodeWalker.GameFiles.RpfEncryption]::OPEN)
function AddDir($parent, $fsPath) {
    foreach ($it in Get-ChildItem -LiteralPath $fsPath) {
        if ($it.PSIsContainer) {
            $sub = [CodeWalker.GameFiles.RpfFile]::CreateDirectory($parent, $it.Name)
            AddDir $sub $it.FullName
        } else {
            [CodeWalker.GameFiles.RpfFile]::CreateFile($parent, $it.Name, [IO.File]::ReadAllBytes($it.FullName), $true) | Out-Null
        }
    }
}
AddDir $rpf.Root $Stage

# verify it re-opens cleanly
$v = New-Object CodeWalker.GameFiles.RpfFile -ArgumentList $rpfPath, "dlc.rpf"; $v.ScanStructure($null, $null)
$count = ($v.AllEntries | Where-Object { $_.GetType().Name -ne 'RpfDirectoryEntry' }).Count
$size = (Get-Item $rpfPath).Length
if ($size -lt 100000 -or $count -lt 6) { throw "pack looks wrong: $size bytes, $count files" }

New-Item -ItemType Directory -Force (Split-Path $Out -Parent) | Out-Null
Copy-Item $rpfPath $Out -Force
Write-Output "OK packed $size bytes, $count files -> $Out"
Write-Output "Install: copy to mods\update\x64\dlcpacks\claudefm\dlc.rpf and add dlcpacks:/claudefm/ to dlclist.xml (see dist\INSTALL.md)"
