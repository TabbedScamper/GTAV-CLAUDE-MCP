<#
  deploy.ps1 - Copy the runtime files into your GTA V install.
  Auto-detects GTA V (Steam / Epic / Rockstar) or pass -GtaPath "D:\...\Grand Theft Auto V".

  Copies:
    pyscript\bridge.py        -> <GTA>\pyscript\bridge.py        (reload in-game with F9)
    pyscript\native_db.json   -> <GTA>\pyscript\native_db.json
    ui_companion\bin\Release\ClaudeChatUI.dll -> <GTA>\scripts\ClaudeChatUI.dll  (SHVDN)

  Prereqs you install separately (this script just checks): ScriptHookV, PyLoaderV 0.6,
  ScriptHookVDotNet (for the C# UI). Run gtav_host.py via run_host.bat.
#>
param([string]$GtaPath = "")

$ErrorActionPreference = "Stop"
$proj = $PSScriptRoot

function Find-Gta {
    $candidates = @()
    # Steam (main + extra library folders)
    try {
        $steam = (Get-ItemProperty 'HKLM:\SOFTWARE\WOW6432Node\Valve\Steam' -ErrorAction SilentlyContinue).InstallPath
        if ($steam) {
            $candidates += Join-Path $steam 'steamapps\common\Grand Theft Auto V'
            $vdf = Join-Path $steam 'steamapps\libraryfolders.vdf'
            if (Test-Path $vdf) {
                foreach ($m in [regex]::Matches((Get-Content $vdf -Raw), '"path"\s*"([^"]+)"')) {
                    $candidates += Join-Path ($m.Groups[1].Value -replace '\\\\','\') 'steamapps\common\Grand Theft Auto V'
                }
            }
        }
    } catch {}
    # Rockstar
    try {
        $r = (Get-ItemProperty 'HKLM:\SOFTWARE\WOW6432Node\Rockstar Games\Grand Theft Auto V' -ErrorAction SilentlyContinue).InstallFolder
        if ($r) { $candidates += $r }
    } catch {}
    # Epic + common spots
    $candidates += 'C:\Program Files\Epic Games\GTAV'
    $candidates += 'C:\Program Files\Rockstar Games\Grand Theft Auto V'
    $candidates += 'C:\Program Files\Rockstar Games\GTAV Enhanced'

    foreach ($c in $candidates) {
        if ($c -and (Test-Path $c)) {
            if ((Test-Path (Join-Path $c 'GTA5.exe')) -or (Test-Path (Join-Path $c 'GTA5_Enhanced.exe')) -or (Test-Path (Join-Path $c 'PlayGTAV.exe'))) {
                return $c
            }
        }
    }
    return $null
}

if (-not $GtaPath) { $GtaPath = Find-Gta }
if (-not $GtaPath -or -not (Test-Path $GtaPath)) {
    Write-Host "Could not auto-detect GTA V. Re-run: .\deploy.ps1 -GtaPath 'X:\path\to\Grand Theft Auto V'" -ForegroundColor Yellow
    exit 1
}

$edition = if (Test-Path (Join-Path $GtaPath 'GTA5_Enhanced.exe')) { 'Enhanced' } else { 'Legacy' }
Write-Host "GTA V found: $GtaPath  [$edition]" -ForegroundColor Green

# Ensure target folders
$pyDir = Join-Path $GtaPath 'pyscript'
$scriptsDir = Join-Path $GtaPath 'scripts'
New-Item -ItemType Directory -Force -Path $pyDir, $scriptsDir | Out-Null

# Copy runtime files
Copy-Item (Join-Path $proj 'pyscript\bridge.py')      $pyDir -Force; Write-Host "  + pyscript\bridge.py"
Copy-Item (Join-Path $proj 'pyscript\native_db.json') $pyDir -Force; Write-Host "  + pyscript\native_db.json"
$dll = Join-Path $proj 'ui_companion\bin\Release\ClaudeChatUI.dll'
if (Test-Path $dll) { Copy-Item $dll $scriptsDir -Force; Write-Host "  + scripts\ClaudeChatUI.dll" }
else { Write-Host "  ! ClaudeChatUI.dll not built (build ui_companion first)" -ForegroundColor Yellow }

# LemonUI.SHVDN3.dll - runtime dependency of the UI; it won't load without it.
$lemon = Join-Path $proj 'ui_companion\bin\Release\LemonUI.SHVDN3.dll'
if (Test-Path $lemon) { Copy-Item $lemon $scriptsDir -Force; Write-Host "  + scripts\LemonUI.SHVDN3.dll" }
else { Write-Host "  ! LemonUI.SHVDN3.dll missing from build output" -ForegroundColor Yellow }

# Claude Radio (in-game NAudio player) + its NAudio dependency.
$radioRel = Join-Path $proj 'radio\ClaudeRadio\bin\Release'
foreach ($f in 'ClaudeRadio.dll','NAudio.dll') {
    $src = Join-Path $radioRel $f
    if (Test-Path $src) { Copy-Item $src $scriptsDir -Force; Write-Host "  + scripts\$f" }
    else { Write-Host "  ! $f not built (build radio\ClaudeRadio first)" -ForegroundColor Yellow }
}

# Clear stale bytecode so F9 reload picks up the new bridge
Remove-Item (Join-Path $pyDir '__pycache__') -Recurse -Force -ErrorAction SilentlyContinue

# Prereq check (informational)
Write-Host "`nPrereq check in $GtaPath :"
foreach ($f in @('ScriptHookV.dll','dinput8.dll','dsound.dll','PyloaderV.asi','ScriptHookVDotNet3.dll')) {
    $present = Test-Path (Join-Path $GtaPath $f)
    Write-Host ("  [{0}] {1}" -f ($(if($present){'x'}else{' '}), $f))
}
Write-Host "`nDone. In-game: press F9 to (re)load the Python bridge. Run run_host.bat to start Claude." -ForegroundColor Green
