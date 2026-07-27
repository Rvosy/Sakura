param(
    [Parameter(Mandatory = $true)]
    [string]$Executable,

    [Parameter(Mandatory = $true)]
    [string]$EvidenceDirectory
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

Add-Type -AssemblyName System.Drawing.Common
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

public static class SakuraBorderlessDragNative {
    [StructLayout(LayoutKind.Sequential)]
    public struct RECT {
        public int Left;
        public int Top;
        public int Right;
        public int Bottom;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct POINT {
        public int X;
        public int Y;
    }

    [DllImport("user32.dll")]
    public static extern bool GetWindowRect(IntPtr window, out RECT rect);

    [DllImport("user32.dll")]
    public static extern bool GetClientRect(IntPtr window, out RECT rect);

    [DllImport("user32.dll")]
    public static extern bool ClientToScreen(IntPtr window, ref POINT point);

    [DllImport("user32.dll")]
    public static extern int GetWindowLong(IntPtr window, int index);

    [DllImport("user32.dll")]
    public static extern int GetWindowRgnBox(IntPtr window, out RECT rect);

    [DllImport("user32.dll")]
    public static extern uint GetDpiForWindow(IntPtr window);

    [DllImport("user32.dll")]
    public static extern bool IsWindowVisible(IntPtr window);

    [DllImport("user32.dll")]
    public static extern bool SetCursorPos(int x, int y);

    [DllImport("user32.dll")]
    public static extern void mouse_event(uint flags, uint x, uint y, uint data, UIntPtr extra);

    [DllImport("user32.dll")]
    public static extern IntPtr SendMessage(IntPtr window, uint message, UIntPtr wParam, IntPtr lParam);

    [DllImport("user32.dll")]
    public static extern bool SetForegroundWindow(IntPtr window);

    [DllImport("user32.dll")]
    public static extern IntPtr GetForegroundWindow();

    [DllImport("user32.dll")]
    public static extern bool SetWindowPos(IntPtr window, IntPtr insertAfter, int x, int y, int width, int height, uint flags);
}
'@

function Get-WindowState {
    param([IntPtr]$WindowHandle)

    $windowRect = [SakuraBorderlessDragNative+RECT]::new()
    $clientRect = [SakuraBorderlessDragNative+RECT]::new()
    $regionRect = [SakuraBorderlessDragNative+RECT]::new()
    if (-not [SakuraBorderlessDragNative]::GetWindowRect($WindowHandle, [ref]$windowRect)) {
        throw "GetWindowRect failed."
    }
    if (-not [SakuraBorderlessDragNative]::GetClientRect($WindowHandle, [ref]$clientRect)) {
        throw "GetClientRect failed."
    }
    $clientOrigin = [SakuraBorderlessDragNative+POINT]::new()
    if (-not [SakuraBorderlessDragNative]::ClientToScreen($WindowHandle, [ref]$clientOrigin)) {
        throw "ClientToScreen failed."
    }
    $rawStyle = [SakuraBorderlessDragNative]::GetWindowLong($WindowHandle, -16)
    $style = [BitConverter]::ToUInt32([BitConverter]::GetBytes($rawStyle), 0)
    return [pscustomobject]@{
        Style = $style
        X = $windowRect.Left
        Y = $windowRect.Top
        Width = $windowRect.Right - $windowRect.Left
        Height = $windowRect.Bottom - $windowRect.Top
        ClientX = $clientOrigin.X - $windowRect.Left
        ClientY = $clientOrigin.Y - $windowRect.Top
        ClientWidth = $clientRect.Right - $clientRect.Left
        ClientHeight = $clientRect.Bottom - $clientRect.Top
        RegionComplexity = [SakuraBorderlessDragNative]::GetWindowRgnBox($WindowHandle, [ref]$regionRect)
    }
}

function Assert-BorderlessState {
    param($State, [string]$Phase)

    $forbiddenFrameBits = [uint32]0x00CF0000
    if (($State.Style -band $forbiddenFrameBits) -ne 0) {
        throw "$Phase retained native caption/frame bits: 0x$($State.Style.ToString('x8'))."
    }
    if (($State.Style -band [uint32]2147483648) -eq 0) {
        throw "$Phase lost WS_POPUP borderless semantics."
    }
    if ($State.ClientX -ne 0 -or $State.ClientY -ne 0 -or
        $State.ClientWidth -ne $State.Width -or $State.ClientHeight -ne $State.Height) {
        throw "$Phase exposed a non-client area: client=$($State.ClientX),$($State.ClientY) $($State.ClientWidth)x$($State.ClientHeight), window=$($State.Width)x$($State.Height)."
    }
    if ($State.RegionComplexity -ne 3) {
        throw "$Phase lost the complex native click-through region: $($State.RegionComplexity)."
    }
}

function Get-NativeHitTest {
    param([int]$X, [int]$Y)

    $packed = [IntPtr](($X -band 0xffff) -bor (($Y -band 0xffff) -shl 16))
    return [SakuraBorderlessDragNative]::SendMessage(
        $script:windowHandle,
        0x0084,
        [UIntPtr]::Zero,
        $packed
    ).ToInt64()
}

function Save-WindowScreenshot {
    param([IntPtr]$WindowHandle, [string]$Path)

    $state = Get-WindowState -WindowHandle $WindowHandle
    $bitmap = [System.Drawing.Bitmap]::new($state.Width, $state.Height)
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    try {
        $graphics.CopyFromScreen($state.X, $state.Y, 0, 0, $bitmap.Size)
        $bitmap.Save($Path, [System.Drawing.Imaging.ImageFormat]::Png)
    }
    finally {
        $graphics.Dispose()
        $bitmap.Dispose()
    }
}

function Assert-NoTitlebarColorBlock {
    param([string]$Path, [string]$Phase)

    $bitmap = [System.Drawing.Bitmap]::new($Path)
    try {
        $longestRun = 0
        $scanHeight = [Math]::Min(120, $bitmap.Height)
        for ($y = 0; $y -lt $scanHeight; $y++) {
            $run = 0
            for ($x = 0; $x -lt $bitmap.Width; $x++) {
                $color = $bitmap.GetPixel($x, $y)
                $looksLikeSystemCaption = $color.R -ge 145 -and $color.R -le 195 -and
                    $color.G -ge 175 -and $color.G -le 220 -and
                    $color.B -ge 205 -and $color.B -le 245
                if ($looksLikeSystemCaption) {
                    $run++
                    $longestRun = [Math]::Max($longestRun, $run)
                }
                else {
                    $run = 0
                }
            }
        }
        if ($longestRun -ge 400) {
            throw "$Phase contains a system-caption color block ($longestRun continuous pixels)."
        }
    }
    finally {
        $bitmap.Dispose()
    }
}

function Assert-CrossProcessPassthrough {
    param([int]$X, [int]$Y, [string]$Phase)

    [void][SakuraBorderlessDragNative]::SetForegroundWindow($script:receiverHandle)
    Start-Sleep -Milliseconds 100
    [void][SakuraBorderlessDragNative]::SetForegroundWindow($script:windowHandle)
    Start-Sleep -Milliseconds 100
    [void][SakuraBorderlessDragNative]::SetCursorPos($X, $Y)
    Start-Sleep -Milliseconds 50
    [SakuraBorderlessDragNative]::mouse_event(0x0002, 0, 0, 0, [UIntPtr]::Zero)
    [SakuraBorderlessDragNative]::mouse_event(0x0004, 0, 0, 0, [UIntPtr]::Zero)
    Start-Sleep -Milliseconds 250
    if ([SakuraBorderlessDragNative]::GetForegroundWindow() -ne $script:receiverHandle) {
        throw "$Phase transparent click did not reach the independent receiver process."
    }
    [void][SakuraBorderlessDragNative]::SetForegroundWindow($script:windowHandle)
    Start-Sleep -Milliseconds 150
}

function Drag-Point {
    param([int]$StartX, [int]$StartY, [int]$EndX, [int]$EndY)

    [void][SakuraBorderlessDragNative]::SetCursorPos($StartX, $StartY)
    [SakuraBorderlessDragNative]::mouse_event(0x0002, 0, 0, 0, [UIntPtr]::Zero)
    Start-Sleep -Milliseconds 100
    for ($step = 1; $step -le 8; $step++) {
        $x = $StartX + [int][Math]::Round(($EndX - $StartX) * $step / 8.0)
        $y = $StartY + [int][Math]::Round(($EndY - $StartY) * $step / 8.0)
        [void][SakuraBorderlessDragNative]::SetCursorPos($x, $y)
        Start-Sleep -Milliseconds 35
    }
    [SakuraBorderlessDragNative]::mouse_event(0x0004, 0, 0, 0, [UIntPtr]::Zero)
    Start-Sleep -Milliseconds 500
}

$resolvedExecutable = (Resolve-Path -LiteralPath $Executable).Path
$resolvedEvidenceDirectory = [System.IO.Path]::GetFullPath($EvidenceDirectory)
[System.IO.Directory]::CreateDirectory($resolvedEvidenceDirectory) | Out-Null
$contractPath = Join-Path (Split-Path $PSScriptRoot -Parent) "frontend\pet\layout-contract.json"
$contract = Get-Content -LiteralPath $contractPath -Raw | ConvertFrom-Json
$process = $null
$receiver = $null

try {
    $receiverCode = @'
Add-Type -AssemblyName System.Windows.Forms
$form = [System.Windows.Forms.Form]::new()
$form.Text = "Sakura Cross Process Click Receiver"
$form.StartPosition = "Manual"
$form.FormBorderStyle = "None"
$form.SetBounds(0, 0, 900, 750)
$form.BackColor = [System.Drawing.Color]::FromArgb(80, 30, 100)
[System.Windows.Forms.Application]::Run($form)
'@
    $encodedReceiver = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($receiverCode))
    $receiver = Start-Process -FilePath (Get-Command pwsh).Source `
        -ArgumentList "-NoProfile", "-EncodedCommand", $encodedReceiver `
        -PassThru `
        -WindowStyle Normal
    $receiverDeadline = [DateTime]::UtcNow.AddSeconds(10)
    do {
        if ($receiver.HasExited) { throw "Independent click receiver exited before startup." }
        $receiver.Refresh()
        $script:receiverHandle = $receiver.MainWindowHandle
        Start-Sleep -Milliseconds 50
    } while ($script:receiverHandle -eq [IntPtr]::Zero -and [DateTime]::UtcNow -lt $receiverDeadline)
    if ($script:receiverHandle -eq [IntPtr]::Zero) { throw "Timed out waiting for click receiver." }

    $process = Start-Process -FilePath $resolvedExecutable `
        -WorkingDirectory (Split-Path $resolvedExecutable -Parent) `
        -PassThru `
        -WindowStyle Normal
    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    $script:windowHandle = [IntPtr]::Zero
    do {
        if ($process.HasExited) { throw "Shell exited before the pet window became ready." }
        $process.Refresh()
        $script:windowHandle = $process.MainWindowHandle
        if ($script:windowHandle -ne [IntPtr]::Zero -and
            [SakuraBorderlessDragNative]::IsWindowVisible($script:windowHandle)) {
            $dpi = [SakuraBorderlessDragNative]::GetDpiForWindow($script:windowHandle)
            $scale = $dpi / 96.0
            $state = Get-WindowState -WindowHandle $script:windowHandle
            $expectedWidth = [int][Math]::Round([double]$contract.viewport.windowSize[0] * $scale)
            $expectedHeight = [int][Math]::Round([double]$contract.viewport.windowSize[1] * $scale)
            if ($state.Width -eq $expectedWidth -and $state.Height -eq $expectedHeight) { break }
        }
        Start-Sleep -Milliseconds 50
    } while ([DateTime]::UtcNow -lt $deadline)
    if ($script:windowHandle -eq [IntPtr]::Zero -or
        $state.Width -ne $expectedWidth -or $state.Height -ne $expectedHeight) {
        throw "Timed out waiting for the stable product pet window."
    }

    Start-Sleep -Milliseconds 800
    $before = Get-WindowState -WindowHandle $script:windowHandle
    if (-not [SakuraBorderlessDragNative]::SetWindowPos(
        $script:receiverHandle,
        [IntPtr]::Zero,
        $before.X,
        $before.Y,
        $before.Width,
        $before.Height,
        0x0010
    )) {
        throw "Failed to place the independent click receiver behind the pet."
    }
    Assert-BorderlessState -State $before -Phase "before drag"
    $transparentX = $before.X + [int][Math]::Round(20 * $scale)
    $transparentY = $before.Y + [int][Math]::Round(20 * $scale)
    Assert-CrossProcessPassthrough -X $transparentX -Y $transparentY -Phase "before drag"
    $beforeScreenshot = Join-Path $resolvedEvidenceDirectory "$($process.Id)-before-drag.png"
    Save-WindowScreenshot -WindowHandle $script:windowHandle -Path $beforeScreenshot
    Assert-NoTitlebarColorBlock -Path $beforeScreenshot -Phase "before drag"

    $dragCandidates = @(@(408, 180), @(408, 120), @(480, 250), @(330, 250))
    $dragPoint = $null
    foreach ($candidate in $dragCandidates) {
        $candidateX = $before.X + [int][Math]::Round($candidate[0] * $scale)
        $candidateY = $before.Y + [int][Math]::Round($candidate[1] * $scale)
        [void][SakuraBorderlessDragNative]::SetCursorPos($candidateX, $candidateY)
        Start-Sleep -Milliseconds 50
        if ((Get-NativeHitTest -X $candidateX -Y $candidateY) -ne -1) {
            $dragPoint = [pscustomobject]@{ X = $candidateX; Y = $candidateY }
            break
        }
    }
    if ($null -eq $dragPoint) { throw "No visible portrait pixel was available for native drag." }
    Drag-Point -StartX $dragPoint.X -StartY $dragPoint.Y `
        -EndX ($dragPoint.X - [int][Math]::Round(180 * $scale)) `
        -EndY ($dragPoint.Y - [int][Math]::Round(100 * $scale))

    $after = Get-WindowState -WindowHandle $script:windowHandle
    if ($before.X -eq $after.X -and $before.Y -eq $after.Y) {
        throw "Native portrait drag did not move the window."
    }
    Assert-BorderlessState -State $after -Phase "after drag"
    if (-not [SakuraBorderlessDragNative]::SetWindowPos(
        $script:receiverHandle,
        [IntPtr]::Zero,
        $after.X,
        $after.Y,
        $after.Width,
        $after.Height,
        0x0010
    )) {
        throw "Failed to move the independent click receiver behind the dragged pet."
    }
    $afterTransparentX = $after.X + [int][Math]::Round(20 * $scale)
    $afterTransparentY = $after.Y + [int][Math]::Round(20 * $scale)
    Assert-CrossProcessPassthrough -X $afterTransparentX -Y $afterTransparentY -Phase "after drag"
    $afterScreenshot = Join-Path $resolvedEvidenceDirectory "$($process.Id)-after-drag.png"
    Save-WindowScreenshot -WindowHandle $script:windowHandle -Path $afterScreenshot
    Assert-NoTitlebarColorBlock -Path $afterScreenshot -Phase "after drag"

    $evidence = [pscustomobject]@{
        ProcessId = $process.Id
        Scale = $scale
        Before = $before
        After = $after
        BeforeScreenshot = $beforeScreenshot
        AfterScreenshot = $afterScreenshot
    }
    $reportPath = Join-Path $resolvedEvidenceDirectory "$($process.Id)-borderless-drag.json"
    $evidence | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $reportPath -Encoding utf8
    $evidence | ConvertTo-Json -Depth 4
}
finally {
    if ($null -ne $process -and -not $process.HasExited) {
        $process.Kill($true)
        $process.WaitForExit()
    }
    if ($null -ne $receiver -and -not $receiver.HasExited) {
        $receiver.Kill($true)
        $receiver.WaitForExit()
    }
}
