param(
    [Parameter(Mandatory = $true)]
    [string]$Executable,

    [Parameter(Mandatory = $true)]
    [string]$EvidenceDirectory,

    [string]$DataRoot = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

Add-Type -AssemblyName System.Drawing.Common
Add-Type -AssemblyName System.Windows.Forms
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

public static class SakuraGeometryGateNative {
    [StructLayout(LayoutKind.Sequential)]
    public struct RECT {
        public int Left;
        public int Top;
        public int Right;
        public int Bottom;
    }

    [DllImport("user32.dll")]
    public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);

    [DllImport("user32.dll")]
    public static extern uint GetDpiForWindow(IntPtr hWnd);

    [DllImport("user32.dll")]
    public static extern bool IsWindowVisible(IntPtr hWnd);

    [DllImport("user32.dll")]
    public static extern bool SetCursorPos(int x, int y);

    [DllImport("user32.dll")]
    public static extern bool SetForegroundWindow(IntPtr hWnd);

    [DllImport("user32.dll")]
    public static extern void mouse_event(uint flags, uint dx, uint dy, uint data, UIntPtr extra);
}
'@

function Get-DataManifestHash {
    param([string]$Root)

    if (-not $Root) {
        return $null
    }
    $resolved = (Resolve-Path -LiteralPath $Root).Path
    $records = Get-ChildItem -LiteralPath $resolved -File -Recurse | ForEach-Object {
        $relative = [System.IO.Path]::GetRelativePath($resolved, $_.FullName).Replace("\", "/")
        $hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        "$relative`t$($_.Length)`t$($_.LastWriteTimeUtc.Ticks)`t$hash"
    }
    $canonical = ($records | Sort-Object) -join "`n"
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($canonical)
    $digest = [System.Security.Cryptography.SHA256]::HashData($bytes)
    return [Convert]::ToHexString($digest).ToLowerInvariant()
}

function Get-DescendantProcesses {
    param([int]$RootPid)

    $all = @(Get-CimInstance Win32_Process)
    $pending = [System.Collections.Generic.Queue[int]]::new()
    $pending.Enqueue($RootPid)
    $descendants = [System.Collections.Generic.List[object]]::new()
    while ($pending.Count -gt 0) {
        $parent = $pending.Dequeue()
        foreach ($child in $all | Where-Object { $_.ParentProcessId -eq $parent }) {
            $descendants.Add($child)
            $pending.Enqueue([int]$child.ProcessId)
        }
    }
    return @($descendants)
}

function Wait-ForMainWindow {
    param([System.Diagnostics.Process]$Process)

    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    do {
        if ($Process.HasExited) {
            throw "Shell exited before its transparent window became visible."
        }
        $Process.Refresh()
        if ($Process.MainWindowHandle -ne [IntPtr]::Zero -and
            [SakuraGeometryGateNative]::IsWindowVisible($Process.MainWindowHandle)) {
            return $Process.MainWindowHandle
        }
        Start-Sleep -Milliseconds 50
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Timed out waiting for the transparent pet window."
}

function Click-Point {
    param([int]$X, [int]$Y)

    [void][SakuraGeometryGateNative]::SetCursorPos($X, $Y)
    [SakuraGeometryGateNative]::mouse_event(0x0002, 0, 0, 0, [UIntPtr]::Zero)
    [SakuraGeometryGateNative]::mouse_event(0x0004, 0, 0, 0, [UIntPtr]::Zero)
}

function Get-WindowBounds {
    param([IntPtr]$WindowHandle)

    $rect = [SakuraGeometryGateNative+RECT]::new()
    if (-not [SakuraGeometryGateNative]::GetWindowRect($WindowHandle, [ref]$rect)) {
        throw "GetWindowRect failed."
    }
    return [pscustomobject]@{
        X = $rect.Left
        Y = $rect.Top
        Width = $rect.Right - $rect.Left
        Height = $rect.Bottom - $rect.Top
    }
}

function Save-WindowScreenshot {
    param([IntPtr]$WindowHandle, [string]$Path)

    $bounds = Get-WindowBounds -WindowHandle $WindowHandle
    $bitmap = [System.Drawing.Bitmap]::new($bounds.Width, $bounds.Height)
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    try {
        $graphics.CopyFromScreen($bounds.X, $bounds.Y, 0, 0, $bitmap.Size)
        $bitmap.Save($Path, [System.Drawing.Imaging.ImageFormat]::Png)
    }
    finally {
        $graphics.Dispose()
        $bitmap.Dispose()
    }
}

$resolvedExecutable = (Resolve-Path -LiteralPath $Executable).Path
$resolvedEvidenceDirectory = [System.IO.Path]::GetFullPath($EvidenceDirectory)
[System.IO.Directory]::CreateDirectory($resolvedEvidenceDirectory) | Out-Null
$contractPath = Join-Path (Split-Path $PSScriptRoot -Parent) "frontend\pet\layout-contract.json"
$contract = Get-Content -LiteralPath $contractPath -Raw | ConvertFrom-Json
$dataBefore = Get-DataManifestHash -Root $DataRoot
$process = $null

try {
    $process = Start-Process -FilePath $resolvedExecutable `
        -WorkingDirectory (Split-Path $resolvedExecutable -Parent) `
        -PassThru `
        -WindowStyle Normal
    $windowHandle = Wait-ForMainWindow -Process $process
    $dpi = [SakuraGeometryGateNative]::GetDpiForWindow($windowHandle)
    $states = [System.Collections.Generic.List[object]]::new()
    $fixedAnchor = $null

    $activationBounds = Get-WindowBounds -WindowHandle $windowHandle
    [void][SakuraGeometryGateNative]::SetForegroundWindow($windowHandle)
    Click-Point `
        -X ($activationBounds.X + [int][Math]::Round(176 * $dpi / 96.0)) `
        -Y ($activationBounds.Y + [int][Math]::Round(180 * $dpi / 96.0))
    Start-Sleep -Milliseconds 250

    $stateNames = @("idle", "bubble", "composer", "expanded")
    for ($stateIndex = 0; $stateIndex -lt $stateNames.Count; $stateIndex++) {
        $stateName = $stateNames[$stateIndex]
        $beforeClickBounds = Get-WindowBounds -WindowHandle $windowHandle
        [void][SakuraGeometryGateNative]::SetForegroundWindow($windowHandle)
        Start-Sleep -Milliseconds 80
        $buttonX = $beforeClickBounds.X + [int][Math]::Round((26 + 31 * $stateIndex) * $dpi / 96.0)
        $buttonY = $beforeClickBounds.Y + $beforeClickBounds.Height - [int][Math]::Round(30 * $dpi / 96.0)
        for ($clickAttempt = 0; $clickAttempt -lt 3; $clickAttempt++) {
            Click-Point -X $buttonX -Y $buttonY
            Start-Sleep -Milliseconds 100
        }
        Start-Sleep -Milliseconds 350
        $process.Refresh()
        $windowHandle = $process.MainWindowHandle
        $bounds = Get-WindowBounds -WindowHandle $windowHandle
        $layout = $contract.states.$stateName
        $expectedWidth = [int][Math]::Round([double]$layout.windowSize[0] * $dpi / 96.0)
        $expectedHeight = [int][Math]::Round([double]$layout.windowSize[1] * $dpi / 96.0)
        if ($bounds.Width -ne $expectedWidth -or $bounds.Height -ne $expectedHeight) {
            $diagnosticScreenshot = Join-Path $resolvedEvidenceDirectory "$($process.Id)-$stateName-mismatch.png"
            Save-WindowScreenshot -WindowHandle $windowHandle -Path $diagnosticScreenshot
            throw "$stateName native bounds mismatch: $($bounds.Width)x$($bounds.Height), expected ${expectedWidth}x${expectedHeight}."
        }
        $anchor = [pscustomobject]@{
            X = $bounds.X + [int][Math]::Round([double]$layout.portraitAnchor[0] * $dpi / 96.0)
            Y = $bounds.Y + [int][Math]::Round([double]$layout.portraitAnchor[1] * $dpi / 96.0)
        }
        if ($null -eq $fixedAnchor) {
            $fixedAnchor = $anchor
        }
        elseif ($anchor.X -ne $fixedAnchor.X -or $anchor.Y -ne $fixedAnchor.Y) {
            throw "$stateName moved the physical portrait anchor."
        }
        $screenshot = Join-Path $resolvedEvidenceDirectory "$($process.Id)-$stateName.png"
        Save-WindowScreenshot -WindowHandle $windowHandle -Path $screenshot
        $states.Add([pscustomobject]@{
            State = $stateName
            X = $bounds.X
            Y = $bounds.Y
            Width = $bounds.Width
            Height = $bounds.Height
            AnchorX = $anchor.X
            AnchorY = $anchor.Y
            Visible = [SakuraGeometryGateNative]::IsWindowVisible($windowHandle)
            Screenshot = $screenshot
        })
    }

    $visibilityBounds = Get-WindowBounds -WindowHandle $windowHandle
    $visibilityX = $visibilityBounds.X + [int][Math]::Round((26 + 31 * 4) * $dpi / 96.0)
    $visibilityY = $visibilityBounds.Y + $visibilityBounds.Height - [int][Math]::Round(30 * $dpi / 96.0)
    Click-Point -X $visibilityX -Y $visibilityY
    $sawHidden = $false
    $sawVisibleAfterHidden = $false
    $visibilityDeadline = [DateTime]::UtcNow.AddSeconds(2)
    do {
        $visible = [SakuraGeometryGateNative]::IsWindowVisible($windowHandle)
        if (-not $visible) {
            $sawHidden = $true
        }
        elseif ($sawHidden) {
            $sawVisibleAfterHidden = $true
            break
        }
        Start-Sleep -Milliseconds 10
    } while ([DateTime]::UtcNow -lt $visibilityDeadline)
    if (-not $sawHidden -or -not $sawVisibleAfterHidden) {
        throw "The visibility probe did not produce a bounded hide/show cycle."
    }

    $descendants = @(Get-DescendantProcesses -RootPid $process.Id)
    $pythonDescendants = @($descendants | Where-Object { $_.Name -match "^python(w)?\.exe$" })
    if ($pythonDescendants.Count -ne 0) {
        throw "The Shell started a Python descendant."
    }
    $descendantIds = @($descendants | ForEach-Object { [int]$_.ProcessId })

    $closeBounds = Get-WindowBounds -WindowHandle $windowHandle
    $closeX = $closeBounds.X + [int][Math]::Round((26 + 31 * 5) * $dpi / 96.0)
    $closeY = $closeBounds.Y + $closeBounds.Height - [int][Math]::Round(30 * $dpi / 96.0)
    Click-Point -X $closeX -Y $closeY
    if (-not $process.WaitForExit(5000)) {
        throw "The Shell did not exit within five seconds."
    }
    Start-Sleep -Milliseconds 500
    $lingering = @($descendantIds | Where-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue })
    if ($lingering.Count -ne 0) {
        throw "Shell/WebView descendants remained after exit: $($lingering -join ', ')."
    }

    $dataAfter = Get-DataManifestHash -Root $DataRoot
    if ($dataBefore -ne $dataAfter) {
        throw "The real data/ manifest changed during acceptance."
    }

    [pscustomobject]@{
        Executable = $resolvedExecutable
        ExitCode = $process.ExitCode
        Dpi = $dpi
        ScaleFactor = $dpi / 96.0
        Screens = @([System.Windows.Forms.Screen]::AllScreens | ForEach-Object {
            [pscustomobject]@{
                DeviceName = $_.DeviceName
                Primary = $_.Primary
                Bounds = $_.Bounds.ToString()
                WorkingArea = $_.WorkingArea.ToString()
            }
        })
        States = @($states)
        PortraitAnchor = $fixedAnchor
        VisibilityProbe = "hidden_then_visible"
        RuntimeDescendants = @($descendants | ForEach-Object { $_.Name })
        PythonDescendants = @($pythonDescendants | ForEach-Object { $_.Name })
        LingeringDescendantPids = $lingering
        DataManifestBefore = $dataBefore
        DataManifestAfter = $dataAfter
    } | ConvertTo-Json -Depth 8
}
finally {
    if ($null -ne $process -and -not $process.HasExited) {
        $process.Kill($true)
        $process.WaitForExit()
    }
}
