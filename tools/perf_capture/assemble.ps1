<#
  assemble.ps1 — gather the reporter-ready perf-capture kit into .\dist\
  Run AFTER building csharp_bridge (so ClaudeBridge.dll + 0Harmony.dll exist).
#>
$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Repo = Resolve-Path (Join-Path $Here "..\..")
$Dist = Join-Path $Here "dist"
New-Item -ItemType Directory -Force -Path $Dist | Out-Null

$bridgeBin = Join-Path $Repo "csharp_bridge\bin\Release\net48"
$copies = @(
    @{ src = Join-Path $bridgeBin "ClaudeBridge.dll"; req = $true  },
    @{ src = Join-Path $bridgeBin "0Harmony.dll";     req = $true  },
    @{ src = Join-Path $Repo "pyscript\native_db.json"; req = $false },
    @{ src = Join-Path $Here "Capture-ELSCPerf.ps1";  req = $true  },
    @{ src = Join-Path $Here "run_capture.bat";       req = $true  },
    @{ src = Join-Path $Here "README.txt";            req = $true  }
)
foreach ($c in $copies) {
    if (Test-Path $c.src) { Copy-Item $c.src $Dist -Force; Write-Host "  + $(Split-Path -Leaf $c.src)" }
    elseif ($c.req) { throw "MISSING required file: $($c.src) (build csharp_bridge first)" }
    else { Write-Host "  - skipped (optional, not found): $(Split-Path -Leaf $c.src)" }
}
Write-Host ""
Write-Host "Kit assembled -> $Dist" -ForegroundColor Green
Write-Host "Zip that folder and send it to a reporter (or zip via: Compress-Archive $Dist\* ELSC-PerfKit.zip)."
