# GTAV-Claude-MCP: capture ONLY the GTA5.exe window to a PNG (on-demand).
# In-process/bridge GDI capture of a D3D window returns black, so this grabs the
# desktop region at the game-window rect (the reliable approach). Prints the saved path.
#   Usage:  powershell -File tools\screenshot.ps1 [outputPath]
param([string]$Out = "$env:TEMP\gta_shot.png")

Add-Type @"
using System; using System.Runtime.InteropServices;
public class WinShot {
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
}
"@
$p = Get-Process GTA5 -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $p) { Write-Error "GTA5.exe is not running"; exit 1 }
$r = New-Object WinShot+RECT
[void][WinShot]::GetWindowRect($p.MainWindowHandle, [ref]$r)
$w = $r.Right - $r.Left; $h = $r.Bottom - $r.Top
if ($w -le 0 -or $h -le 0) { Write-Error "Bad window rect (minimized?)"; exit 1 }

Add-Type -AssemblyName System.Drawing
$bmp = New-Object System.Drawing.Bitmap $w, $h
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($r.Left, $r.Top, 0, 0, (New-Object System.Drawing.Size $w, $h))
$bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()
Write-Output $Out
