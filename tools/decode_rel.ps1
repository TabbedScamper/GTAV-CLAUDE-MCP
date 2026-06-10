<#
  decode_rel.ps1 - Headlessly convert a GTA audio .rel/dat54/dat151 file to readable XML
  using CodeWalker.Core.dll (no GUI). Loads a name dictionary into JenkIndex first so
  hashes resolve to readable names.

  Usage:
    powershell -ExecutionPolicy Bypass -File decode_rel.ps1 -In <file.rel> -Out <out.xml> -CwDir <CodeWalker folder> [-Names names.txt]
#>
param(
    [Parameter(Mandatory=$true)][string]$In,
    [Parameter(Mandatory=$true)][string]$Out,
    [Parameter(Mandatory=$true)][string]$CwDir,
    [string]$Names = ""
)
$ErrorActionPreference = "Stop"
try {
    # Unblock once (MOTW would make LoadFrom fail with 0x80131515). Cheap + idempotent.
    Get-ChildItem -LiteralPath $CwDir -Include *.dll,*.exe -Recurse -ErrorAction SilentlyContinue | Unblock-File -ErrorAction SilentlyContinue
    $asm = [System.Reflection.Assembly]::LoadFrom((Join-Path $CwDir "CodeWalker.Core.dll"))

    # Load name dictionaries into JenkIndex so hash_XXXX resolves to names.
    foreach ($f in @((Join-Path $CwDir "strings.txt"), $Names)) {
        if ($f -and (Test-Path $f)) {
            foreach ($line in [System.IO.File]::ReadLines($f)) {
                if ($line) { [CodeWalker.GameFiles.JenkIndex]::Ensure($line) | Out-Null }
            }
        }
    }

    $bytes = [System.IO.File]::ReadAllBytes($In)
    $name  = [System.IO.Path]::GetFileName($In)
    $entry = New-Object CodeWalker.GameFiles.RpfBinaryFileEntry
    $entry.Name = $name; $entry.NameLower = $name.ToLower(); $entry.Path = $In
    $rel = New-Object CodeWalker.GameFiles.RelFile $entry
    $rel.Load($bytes, $entry)
    $xml = [CodeWalker.GameFiles.RelXml]::GetXml($rel)
    [System.IO.File]::WriteAllText($Out, $xml)
    Write-Output ("OK reldatas=" + $rel.RelDatas.Count + " chars=" + $xml.Length)
} catch {
    Write-Output ("ERROR: " + $_.Exception.Message)
    if ($_.Exception.InnerException) { Write-Output ("INNER: " + $_.Exception.InnerException.Message) }
    exit 1
}
