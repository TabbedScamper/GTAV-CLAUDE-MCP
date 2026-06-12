# GTAV-Claude-MCP: white-screen UI preview harness.
# Triggers ExtendedLSC to paint the screen white and draw the HUD at a posed value, screenshots it,
# then clears immediately. Lets Claude design/iterate HUD art without the player driving.
#   Usage:  powershell -File tools\hud_preview.ps1 "<rpm>;<gear>;<mph>;<nos>" [outputPath]
#   Example: powershell -File tools\hud_preview.ps1 "0.8;5;120;0.66" C:\Temp\hud.png
param(
  [string]$Pose = "0.8;5;120;0.66",
  [string]$Out  = "$env:TEMP\hud_preview.png"
)
$gameDir  = "C:\Program Files\Rockstar Games\Grand Theft Auto V Legacy\scripts\ExtendedLSC"
$trigger  = Join-Path $gameDir "ui_preview.txt"
$shot     = Join-Path $PSScriptRoot "screenshot.ps1"

Set-Content -Path $trigger -Value $Pose -Encoding ascii
Start-Sleep -Milliseconds 1100
& powershell -File $shot $Out | Out-Null
Remove-Item -Path $trigger -ErrorAction SilentlyContinue
Write-Output $Out
