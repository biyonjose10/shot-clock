# Move the pointer and click, in screen coordinates.
#
# The take is driven at the OS level rather than through the browser extension:
# attaching a debugger puts a "'Claude' started debugging this browser" bar
# across the top of the window, which cannot be in the video.
param(
  [Parameter(Mandatory = $true)][int]$X,
  [Parameter(Mandatory = $true)][int]$Y,
  [int]$SettleMs = 120
)

Add-Type @'
using System;
using System.Runtime.InteropServices;
public class Click {
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint x, uint y, uint d, int e);
  public const uint DOWN = 0x0002, UP = 0x0004;
}
'@

[Click]::SetCursorPos($X, $Y) | Out-Null
Start-Sleep -Milliseconds $SettleMs
[Click]::mouse_event([Click]::DOWN, 0, 0, 0, 0)
Start-Sleep -Milliseconds 40
[Click]::mouse_event([Click]::UP, 0, 0, 0, 0)
"clicked $X,$Y"
