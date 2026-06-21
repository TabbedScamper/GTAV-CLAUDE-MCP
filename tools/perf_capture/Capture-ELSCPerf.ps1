<#
  Capture-ELSCPerf.ps1  -  one-shot ExtendedLSC performance capture for bug reports.

  Talks to the ClaudeBridge profiler (must be running in GTA - see README.txt) over 127.0.0.1:27015 and
  records: frame-time/FPS, per-script CPU cost, per-native call counts (names resolved), per-ELSC-method
  timing, the list of loaded C# mods, and your system specs. Writes everything to ELSC_perf_report.txt.

  Run it (via run_capture.bat) WHILE the game is in the state that drops your FPS (driving, menu open, etc).
  Total capture time ~40s - keep playing in the laggy state the whole time.
#>
param(
    [int]$PerfSeconds   = 10,
    [int]$NativeSeconds = 10,
    [int]$MethodSeconds = 10,
    [int]$FrameSeconds  = 8,
    [string]$Port       = "27015"
)

$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$ReportPath = Join-Path $Here "ELSC_perf_report.txt"
$Report = New-Object System.Collections.Generic.List[string]
function Line($s = "") { $Report.Add($s); Write-Host $s }

# ---- bridge protocol (4-byte LE length + JSON) ----
function Send-Bridge($cmd, $params) {
    $client = New-Object System.Net.Sockets.TcpClient
    try { $client.Connect("127.0.0.1", [int]$Port) }
    catch { throw "Could not connect to the profiler on 127.0.0.1:$Port. Is GTA running with ClaudeBridge.dll in scripts\? (See README.txt)" }
    try {
        $ns = $client.GetStream()
        if ($null -eq $params) { $params = @{} }
        $json = (@{ command = $cmd; params = $params } | ConvertTo-Json -Compress -Depth 12)
        $body = [Text.Encoding]::UTF8.GetBytes($json)
        $ns.Write([BitConverter]::GetBytes([int]$body.Length), 0, 4)
        $ns.Write($body, 0, $body.Length)
        $lenBuf = New-Object byte[] 4; $got = 0
        while ($got -lt 4) { $r = $ns.Read($lenBuf, $got, 4 - $got); if ($r -le 0) { throw "bridge closed" }; $got += $r }
        $n = [BitConverter]::ToInt32($lenBuf, 0)
        $buf = New-Object byte[] $n; $got = 0
        while ($got -lt $n) { $r = $ns.Read($buf, $got, $n - $got); if ($r -le 0) { throw "bridge closed" }; $got += $r }
        $obj = [Text.Encoding]::UTF8.GetString($buf) | ConvertFrom-Json
        if ($obj.PSObject.Properties.Name -contains "error") { throw "bridge error for '$cmd': $($obj.error)" }
        return $obj.result
    } finally { $client.Close() }
}

# ---- native hash -> name (optional native_db.json) ----
$HashMap = @{}
$dbPath = Join-Path $Here "native_db.json"
if (Test-Path $dbPath) {
    Write-Host "Loading native name database..."
    try {
        $db = Get-Content $dbPath -Raw | ConvertFrom-Json
        foreach ($prop in $db.natives.PSObject.Properties) {
            $h = $prop.Value.hashes
            if ($h.legacy)   { $HashMap[$h.legacy.ToUpper()]   = "$($prop.Value.namespace)::$($prop.Name)" }
            if ($h.enhanced) { $HashMap[$h.enhanced.ToUpper()] = "$($prop.Value.namespace)::$($prop.Name)" }
        }
        Write-Host "  resolved $($HashMap.Count) native hashes."
    } catch { Write-Host "  (could not parse native_db.json; hashes will stay hex)" }
}
function Resolve-Native($hex) { if ($hex -and $HashMap.ContainsKey($hex.ToUpper())) { return $HashMap[$hex.ToUpper()] } else { return $hex } }

# ---- capture ----
Line "================ ExtendedLSC performance report ================"
Line ("captured (local clock): " + (Get-Date).ToString("yyyy-MM-dd HH:mm:ss"))
Line ""

Line "---- system ----"
try {
    $cpu = (Get-CimInstance Win32_Processor | Select-Object -First 1).Name
    $gpu = (Get-CimInstance Win32_VideoController | Where-Object { $_.Name } | ForEach-Object { $_.Name }) -join ", "
    $ramGB = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 1)
    $os = (Get-CimInstance Win32_OperatingSystem).Caption
    Line "CPU : $cpu"
    Line "GPU : $gpu"
    Line "RAM : $ramGB GB"
    Line "OS  : $os"
} catch { Line "  (system info unavailable: $($_.Exception.Message))" }

try {
    $proc = Get-Process GTA5 -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($proc -and $proc.Path) {
        $gdir = Split-Path -Parent $proc.Path
        Line "Game: $($proc.Path)"
        $shvLog = Join-Path $gdir "ScriptHookV.log"
        if (Test-Path $shvLog) { $first = (Get-Content $shvLog -TotalCount 1); Line "ScriptHookV: $first" }
        $scriptsDir = Join-Path $gdir "scripts"
        if (Test-Path $scriptsDir) {
            $mods = Get-ChildItem $scriptsDir -Filter *.dll | Select-Object -ExpandProperty Name
            Line ("scripts\*.dll : " + ($mods -join ", "))
        }
        $asis = Get-ChildItem $gdir -Filter *.asi -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name
        if ($asis) { Line ("root .asi    : " + ($asis -join ", ")) }
    } else { Line "Game: GTA5.exe not found running." }
} catch { Line "  (game info unavailable: $($_.Exception.Message))" }
Line ""

$st = Send-Bridge "profiler_status" @{}
Line "---- profiler ----"
Line "patched=$($st.patched)  harmony=$($st.harmony)  init_error=$($st.init_error)"
Line ""

$sm = Send-Bridge "scripts_managed" @{}
Line "---- loaded C# mods ($($sm.count)) ----"
foreach ($s in $sm.scripts) { Line ("  " + $s.name + "   [" + (Split-Path -Leaf $s.filename) + "]") }
Line ""

Line "---- frame time (sampling $FrameSeconds s - keep playing in the laggy state) ----"
Send-Bridge "frametime_reset" @{} | Out-Null
Start-Sleep -Seconds $FrameSeconds
$ft = Send-Bridge "frametime" @{}
Line ("avg {0} ms ({1} FPS), median {2} ms, p99 {3} ms, max {4} ms, hitches {5} ({6} pct)" -f `
    $ft.avg_ms, $ft.avg_fps, $ft.median_ms, $ft.p99_ms, $ft.max_ms, $ft.hitches, $ft.hitch_pct)
Line ""

Line "---- per-script CPU cost (sampling $PerfSeconds s) ----"
Send-Bridge "scripts_perf_reset" @{} | Out-Null
Start-Sleep -Seconds $PerfSeconds
$sp = Send-Bridge "scripts_perf" @{}
Line ("{0,-28} {1,8} {2,9} {3,9}" -f "script", "avg_ms", "max_ms", "ms_per_s")
foreach ($r in $sp.scripts) { Line ("{0,-28} {1,8} {2,9} {3,9}" -f $r.script, $r.avg_ms, $r.max_ms, $r.ms_per_sec) }
Line ""

Line "---- ELSC native calls (counting $NativeSeconds s) ----"
Send-Bridge "natprof_start" @{ per_hash = $true } | Out-Null
Start-Sleep -Seconds $NativeSeconds
$np = Send-Bridge "natprof_report" @{ top = 20 }
Send-Bridge "natprof_stop" @{} | Out-Null
Line ("total native calls per sec (all scripts): {0}" -f $np.total_calls_per_sec)
foreach ($s in $np.scripts) {
    Line ("  [{0}] {1} calls ({2} per sec)" -f $s.script, $s.native_calls, $s.calls_per_sec)
    if ($s.top_natives) { foreach ($h in $s.top_natives) { Line ("      {0,7}  {1}" -f $h.calls, (Resolve-Native $h.hash)) } }
}
Line ""

Line "---- ELSC per-method timing (sampling $MethodSeconds s) ----"
$startRes = Send-Bridge "methprof_start" @{}
Send-Bridge "methprof_clear" @{} | Out-Null
Start-Sleep -Seconds $MethodSeconds
$mp = Send-Bridge "methprof_report" @{ top = 25 }
Send-Bridge "methprof_stop" @{} | Out-Null
Line ("patched {0} methods" -f $startRes.patched_count)
Line ("{0,-34} {1,8} {2,9} {3,9}" -f "method", "calls", "avg_ms", "ms_per_s")
foreach ($m in $mp.methods) { Line ("{0,-34} {1,8} {2,9} {3,9}" -f $m.method, $m.calls, $m.avg_ms, $m.ms_per_sec) }
Line ""
Line "================ end of report ================"

[System.IO.File]::WriteAllLines($ReportPath, $Report)
Write-Host ""
Write-Host "Saved report -> $ReportPath" -ForegroundColor Green
Write-Host "Please send that file to the mod author." -ForegroundColor Green
