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

public static class SakuraInteractionGateNative {
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
    public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);

    [DllImport("user32.dll")]
    public static extern uint GetDpiForWindow(IntPtr hWnd);

    [DllImport("user32.dll")]
    public static extern bool IsWindowVisible(IntPtr hWnd);

    [DllImport("user32.dll")]
    public static extern bool SetCursorPos(int x, int y);

    [DllImport("user32.dll")]
    public static extern void mouse_event(uint flags, uint dx, uint dy, uint data, UIntPtr extra);

    [DllImport("user32.dll")]
    public static extern IntPtr WindowFromPoint(POINT point);

    [DllImport("user32.dll")]
    public static extern IntPtr GetAncestor(IntPtr hWnd, uint flags);

    [DllImport("user32.dll")]
    public static extern bool SetForegroundWindow(IntPtr hWnd);

    [DllImport("user32.dll")]
    public static extern IntPtr GetForegroundWindow();

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    public static extern IntPtr LoadKeyboardLayout(string id, uint flags);

    [DllImport("user32.dll")]
    public static extern bool PostMessage(IntPtr hWnd, uint message, UIntPtr wParam, IntPtr lParam);
}
'@

function Get-DataManifestHash {
    param([string]$Root)

    if (-not $Root) { return $null }
    $resolved = (Resolve-Path -LiteralPath $Root).Path
    $records = Get-ChildItem -LiteralPath $resolved -File -Recurse | ForEach-Object {
        $relative = [System.IO.Path]::GetRelativePath($resolved, $_.FullName).Replace("\", "/")
        $hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        "$relative`t$($_.Length)`t$($_.LastWriteTimeUtc.Ticks)`t$hash"
    }
    $canonical = ($records | Sort-Object) -join "`n"
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($canonical)
    return [Convert]::ToHexString([System.Security.Cryptography.SHA256]::HashData($bytes)).ToLowerInvariant()
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
        if ($Process.HasExited) { throw "Shell exited before its transparent window became visible." }
        $Process.Refresh()
        if ($Process.MainWindowHandle -ne [IntPtr]::Zero -and
            [SakuraInteractionGateNative]::IsWindowVisible($Process.MainWindowHandle)) {
            return $Process.MainWindowHandle
        }
        Start-Sleep -Milliseconds 50
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Timed out waiting for the transparent pet window."
}

function Get-WindowBounds {
    param([IntPtr]$WindowHandle)

    $rect = [SakuraInteractionGateNative+RECT]::new()
    if (-not [SakuraInteractionGateNative]::GetWindowRect($WindowHandle, [ref]$rect)) {
        throw "GetWindowRect failed."
    }
    return [pscustomobject]@{
        X = $rect.Left
        Y = $rect.Top
        Width = $rect.Right - $rect.Left
        Height = $rect.Bottom - $rect.Top
    }
}

function Wait-ForStablePetBounds {
    param([System.Diagnostics.Process]$Process, [IntPtr]$WindowHandle, [int]$ExpectedWidth, [int]$ExpectedHeight)

    $deadline = [DateTime]::UtcNow.AddSeconds(5)
    $matchingSamples = 0
    do {
        $Process.Refresh()
        if ($Process.MainWindowHandle -ne [IntPtr]::Zero) {
            $WindowHandle = $Process.MainWindowHandle
        }
        $bounds = Get-WindowBounds -WindowHandle $WindowHandle
        if ($bounds.Width -eq $ExpectedWidth -and $bounds.Height -eq $ExpectedHeight) {
            $matchingSamples += 1
            if ($matchingSamples -ge 3) { return $WindowHandle }
        }
        else {
            $matchingSamples = 0
        }
        Start-Sleep -Milliseconds 50
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Pet window did not settle at ${ExpectedWidth}x${ExpectedHeight}."
}

function Assert-BoundsEqual {
    param($Expected, $Actual, [string]$Message)

    if ($Expected.X -ne $Actual.X -or $Expected.Y -ne $Actual.Y -or
        $Expected.Width -ne $Actual.Width -or $Expected.Height -ne $Actual.Height) {
        throw "$Message Expected $($Expected.X),$($Expected.Y) $($Expected.Width)x$($Expected.Height), got $($Actual.X),$($Actual.Y) $($Actual.Width)x$($Actual.Height)."
    }
}

function Convert-LogicalPoint {
    param($Bounds, [double]$Scale, [double]$X, [double]$Y)

    return [pscustomobject]@{
        X = $Bounds.X + [int][Math]::Round($X * $Scale)
        Y = $Bounds.Y + [int][Math]::Round($Y * $Scale)
    }
}

function Get-RootWindowAtPoint {
    param([int]$X, [int]$Y)

    $point = [SakuraInteractionGateNative+POINT]::new()
    $point.X = $X
    $point.Y = $Y
    $hit = [SakuraInteractionGateNative]::WindowFromPoint($point)
    if ($hit -eq [IntPtr]::Zero) { return [IntPtr]::Zero }
    return [SakuraInteractionGateNative]::GetAncestor($hit, 2)
}

function Assert-HitOwnership {
    param([IntPtr]$WindowHandle, $Point, [bool]$ExpectedOwned, [string]$Name)

    $owned = (Get-RootWindowAtPoint -X $Point.X -Y $Point.Y) -eq $WindowHandle
    if ($owned -ne $ExpectedOwned) {
        throw "$Name hit ownership mismatch at $($Point.X),$($Point.Y): owned=$owned."
    }
}

function Click-Point {
    param([int]$X, [int]$Y)

    [void][SakuraInteractionGateNative]::SetCursorPos($X, $Y)
    [SakuraInteractionGateNative]::mouse_event(0x0002, 0, 0, 0, [UIntPtr]::Zero)
    [SakuraInteractionGateNative]::mouse_event(0x0004, 0, 0, 0, [UIntPtr]::Zero)
}

function Drag-Point {
    param([int]$StartX, [int]$StartY, [int]$EndX, [int]$EndY)

    [void][SakuraInteractionGateNative]::SetCursorPos($StartX, $StartY)
    [SakuraInteractionGateNative]::mouse_event(0x0002, 0, 0, 0, [UIntPtr]::Zero)
    Start-Sleep -Milliseconds 100
    for ($step = 1; $step -le 8; $step++) {
        $x = $StartX + [int][Math]::Round(($EndX - $StartX) * $step / 8.0)
        $y = $StartY + [int][Math]::Round(($EndY - $StartY) * $step / 8.0)
        [void][SakuraInteractionGateNative]::SetCursorPos($x, $y)
        Start-Sleep -Milliseconds 30
    }
    [SakuraInteractionGateNative]::mouse_event(0x0004, 0, 0, 0, [UIntPtr]::Zero)
    Start-Sleep -Milliseconds 350
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

function Switch-State {
    param([IntPtr]$WindowHandle, $Contract, [double]$Scale, [string]$CurrentState, [string]$TargetState)

    $stateNames = @("idle", "bubble", "composer", "expanded")
    $targetIndex = [Array]::IndexOf($stateNames, $TargetState)
    if ($targetIndex -lt 0) { throw "Unknown target state $TargetState." }
    [void][SakuraInteractionGateNative]::SetForegroundWindow($WindowHandle)
    Start-Sleep -Milliseconds 120
    for ($attempt = 0; $attempt -lt 3; $attempt++) {
        $sourceState = if ($attempt -eq 0) { $CurrentState } else { $TargetState }
        $bounds = Get-WindowBounds -WindowHandle $WindowHandle
        $layout = $Contract.states.$sourceState
        $offsetX = [int]$Contract.viewport.portraitAnchor[0] - [int]$layout.portraitAnchor[0]
        $offsetY = [int]$Contract.viewport.portraitAnchor[1] - [int]$layout.portraitAnchor[1]
        $controls = $layout.controlsRect
        $point = Convert-LogicalPoint -Bounds $bounds -Scale $Scale `
            -X ($offsetX + [double]$controls[0] + 18 + 31 * $targetIndex) `
            -Y ($offsetY + [double]$controls[1] + 19)
        Click-Point -X $point.X -Y $point.Y
        Start-Sleep -Milliseconds 180
    }
    Start-Sleep -Milliseconds 350
    return $TargetState
}

function Set-WindowKeyboardLayout {
    param([IntPtr]$WindowHandle, [string]$LayoutId)

    $layout = [SakuraInteractionGateNative]::LoadKeyboardLayout($LayoutId, 1)
    if ($layout -eq [IntPtr]::Zero) { throw "Failed to load keyboard layout $LayoutId." }
    if (-not [SakuraInteractionGateNative]::PostMessage($WindowHandle, 0x0050, [UIntPtr]::Zero, $layout)) {
        throw "Failed to request keyboard layout $LayoutId for the pet window."
    }
    Start-Sleep -Milliseconds 250
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
    $dpi = [SakuraInteractionGateNative]::GetDpiForWindow($windowHandle)
    $scale = $dpi / 96.0
    $windowHandle = Wait-ForStablePetBounds -Process $process -WindowHandle $windowHandle `
        -ExpectedWidth ([int][Math]::Round([double]$contract.viewport.windowSize[0] * $scale)) `
        -ExpectedHeight ([int][Math]::Round([double]$contract.viewport.windowSize[1] * $scale))
    $currentState = "idle"
    $bounds = Get-WindowBounds -WindowHandle $windowHandle

    $transparentPoint = Convert-LogicalPoint -Bounds $bounds -Scale $scale -X 20 -Y 20
    $portraitPoint = Convert-LogicalPoint -Bounds $bounds -Scale $scale -X 480 -Y 450
    $idleControlsPoint = Convert-LogicalPoint -Bounds $bounds -Scale $scale -X 330 -Y 649
    Assert-HitOwnership -WindowHandle $windowHandle -Point $transparentPoint -ExpectedOwned $false -Name "idle transparent"
    Assert-HitOwnership -WindowHandle $windowHandle -Point $portraitPoint -ExpectedOwned $true -Name "idle portrait"
    Assert-HitOwnership -WindowHandle $windowHandle -Point $idleControlsPoint -ExpectedOwned $true -Name "idle controls"
    $backgroundReceiver = Get-RootWindowAtPoint -X $transparentPoint.X -Y $transparentPoint.Y
    if ($backgroundReceiver -eq [IntPtr]::Zero -or $backgroundReceiver -eq $windowHandle) {
        throw "No real background click receiver was available under the transparent point."
    }
    [void][SakuraInteractionGateNative]::SetForegroundWindow($windowHandle)
    Start-Sleep -Milliseconds 150
    Click-Point -X $transparentPoint.X -Y $transparentPoint.Y
    Start-Sleep -Milliseconds 150
    if ([SakuraInteractionGateNative]::GetForegroundWindow() -eq $windowHandle) {
        throw "The transparent-area click did not activate the real background receiver."
    }

    $currentState = Switch-State -WindowHandle $windowHandle -Contract $contract -Scale $scale -CurrentState $currentState -TargetState "bubble"
    $bounds = Get-WindowBounds -WindowHandle $windowHandle
    $bubblePoint = Convert-LogicalPoint -Bounds $bounds -Scale $scale -X 300 -Y 500
    Assert-HitOwnership -WindowHandle $windowHandle -Point $bubblePoint -ExpectedOwned $true -Name "bubble"
    Assert-HitOwnership -WindowHandle $windowHandle -Point (Convert-LogicalPoint -Bounds $bounds -Scale $scale -X 20 -Y 20) -ExpectedOwned $false -Name "bubble transparent"
    Assert-HitOwnership -WindowHandle $windowHandle -Point (Convert-LogicalPoint -Bounds $bounds -Scale $scale -X 184 -Y 450) -ExpectedOwned $false -Name "bubble rounded corner transparent"
    Assert-HitOwnership -WindowHandle $windowHandle -Point (Convert-LogicalPoint -Bounds $bounds -Scale $scale -X 210 -Y 450) -ExpectedOwned $true -Name "bubble rounded edge owned"

    $beforeBubbleDrag = Get-WindowBounds -WindowHandle $windowHandle
    Drag-Point -StartX $bubblePoint.X -StartY $bubblePoint.Y -EndX ($bubblePoint.X - 90) -EndY ($bubblePoint.Y - 50)
    $afterBubbleDrag = Get-WindowBounds -WindowHandle $windowHandle
    if ($beforeBubbleDrag.X -eq $afterBubbleDrag.X -and $beforeBubbleDrag.Y -eq $afterBubbleDrag.Y) {
        throw "Bubble drag did not move the native window."
    }

    $currentState = Switch-State -WindowHandle $windowHandle -Contract $contract -Scale $scale -CurrentState $currentState -TargetState "composer"
    $bounds = Get-WindowBounds -WindowHandle $windowHandle
    Assert-BoundsEqual -Expected $afterBubbleDrag -Actual $bounds -Message "State switch reverted the bubble drag."
    $inputPoint = Convert-LogicalPoint -Bounds $bounds -Scale $scale -X 260 -Y 572
    $sendPoint = Convert-LogicalPoint -Bounds $bounds -Scale $scale -X 745 -Y 572
    $controlsPoint = Convert-LogicalPoint -Bounds $bounds -Scale $scale -X 65 -Y 649
    Assert-HitOwnership -WindowHandle $windowHandle -Point (Convert-LogicalPoint -Bounds $bounds -Scale $scale -X 184 -Y 546) -ExpectedOwned $false -Name "input rounded corner transparent"
    Assert-HitOwnership -WindowHandle $windowHandle -Point (Convert-LogicalPoint -Bounds $bounds -Scale $scale -X 202 -Y 546) -ExpectedOwned $true -Name "input rounded edge owned"
    Assert-HitOwnership -WindowHandle $windowHandle -Point $inputPoint -ExpectedOwned $true -Name "composer input"
    Assert-HitOwnership -WindowHandle $windowHandle -Point $sendPoint -ExpectedOwned $true -Name "composer send"
    Assert-HitOwnership -WindowHandle $windowHandle -Point $controlsPoint -ExpectedOwned $true -Name "composer controls"

    [void][SakuraInteractionGateNative]::SetForegroundWindow($windowHandle)
    Set-WindowKeyboardLayout -WindowHandle $windowHandle -LayoutId "00000409"
    Click-Point -X $inputPoint.X -Y $inputPoint.Y
    Start-Sleep -Milliseconds 150
    [System.Windows.Forms.SendKeys]::SendWait("focus")
    Start-Sleep -Milliseconds 150
    $englishScreenshot = Join-Path $resolvedEvidenceDirectory "$($process.Id)-english-input.png"
    Save-WindowScreenshot -WindowHandle $windowHandle -Path $englishScreenshot

    foreach ($nonDragPoint in @($inputPoint, $sendPoint, $controlsPoint)) {
        $before = Get-WindowBounds -WindowHandle $windowHandle
        Drag-Point -StartX $nonDragPoint.X -StartY $nonDragPoint.Y -EndX ($nonDragPoint.X - 80) -EndY ($nonDragPoint.Y - 50)
        $after = Get-WindowBounds -WindowHandle $windowHandle
        Assert-BoundsEqual -Expected $before -Actual $after -Message "Interactive region started a native drag."
    }

    $beforeDrag = Get-WindowBounds -WindowHandle $windowHandle
    $dragPoint = Convert-LogicalPoint -Bounds $beforeDrag -Scale $scale -X 480 -Y 450
    Drag-Point -StartX $dragPoint.X -StartY $dragPoint.Y -EndX ($dragPoint.X - 180) -EndY ($dragPoint.Y - 100)
    $afterDrag = Get-WindowBounds -WindowHandle $windowHandle
    if ($beforeDrag.X -eq $afterDrag.X -and $beforeDrag.Y -eq $afterDrag.Y) {
        throw "Portrait drag did not move the native window."
    }
    $draggedAnchor = [pscustomobject]@{
        X = $afterDrag.X + [int][Math]::Round([double]$contract.viewport.portraitAnchor[0] * $scale)
        Y = $afterDrag.Y + [int][Math]::Round([double]$contract.viewport.portraitAnchor[1] * $scale)
    }

    $anchorStates = [System.Collections.Generic.List[object]]::new()
    foreach ($state in @("idle", "bubble", "composer", "expanded")) {
        $currentState = Switch-State -WindowHandle $windowHandle -Contract $contract -Scale $scale -CurrentState $currentState -TargetState $state
        $stateBounds = Get-WindowBounds -WindowHandle $windowHandle
        $anchor = [pscustomobject]@{
            X = $stateBounds.X + [int][Math]::Round([double]$contract.viewport.portraitAnchor[0] * $scale)
            Y = $stateBounds.Y + [int][Math]::Round([double]$contract.viewport.portraitAnchor[1] * $scale)
        }
        if ($anchor.X -ne $draggedAnchor.X -or $anchor.Y -ne $draggedAnchor.Y) {
            throw "$state moved the physical portrait anchor after drag."
        }
        $anchorStates.Add([pscustomobject]@{ State = $state; AnchorX = $anchor.X; AnchorY = $anchor.Y })
    }

    $currentState = Switch-State -WindowHandle $windowHandle -Contract $contract -Scale $scale -CurrentState $currentState -TargetState "composer"
    $bounds = Get-WindowBounds -WindowHandle $windowHandle
    $inputPoint = Convert-LogicalPoint -Bounds $bounds -Scale $scale -X 260 -Y 572
    Click-Point -X $inputPoint.X -Y $inputPoint.Y
    Start-Sleep -Milliseconds 120
    [System.Windows.Forms.SendKeys]::SendWait("%{TAB}")
    Start-Sleep -Milliseconds 500
    $leftForAltTab = [SakuraInteractionGateNative]::GetForegroundWindow() -ne $windowHandle
    [System.Windows.Forms.SendKeys]::SendWait("%{TAB}")
    Start-Sleep -Milliseconds 500
    if ([SakuraInteractionGateNative]::GetForegroundWindow() -ne $windowHandle) {
        [void][SakuraInteractionGateNative]::SetForegroundWindow($windowHandle)
        Start-Sleep -Milliseconds 250
    }
    [System.Windows.Forms.SendKeys]::SendWait("A")
    Start-Sleep -Milliseconds 150
    if (-not $leftForAltTab) { throw "Alt+Tab did not leave the pet window." }
    $altTabScreenshot = Join-Path $resolvedEvidenceDirectory "$($process.Id)-alt-tab-input.png"
    Save-WindowScreenshot -WindowHandle $windowHandle -Path $altTabScreenshot

    $layout = $contract.states.$currentState
    $offsetX = [int]$contract.viewport.portraitAnchor[0] - [int]$layout.portraitAnchor[0]
    $offsetY = [int]$contract.viewport.portraitAnchor[1] - [int]$layout.portraitAnchor[1]
    $visibilityPoint = Convert-LogicalPoint -Bounds (Get-WindowBounds -WindowHandle $windowHandle) -Scale $scale `
        -X ($offsetX + [double]$layout.controlsRect[0] + 18 + 31 * 4) `
        -Y ($offsetY + [double]$layout.controlsRect[1] + 19)
    Click-Point -X $visibilityPoint.X -Y $visibilityPoint.Y
    $sawHidden = $false
    $visibilityDeadline = [DateTime]::UtcNow.AddSeconds(2)
    do {
        $visible = [SakuraInteractionGateNative]::IsWindowVisible($windowHandle)
        if (-not $visible) { $sawHidden = $true }
        if ($sawHidden -and $visible) { break }
        Start-Sleep -Milliseconds 10
    } while ([DateTime]::UtcNow -lt $visibilityDeadline)
    if (-not $sawHidden -or -not [SakuraInteractionGateNative]::IsWindowVisible($windowHandle)) {
        throw "hide/show did not complete."
    }
    Start-Sleep -Milliseconds 300
    [System.Windows.Forms.SendKeys]::SendWait("H")
    Start-Sleep -Milliseconds 150
    $visibilityScreenshot = Join-Path $resolvedEvidenceDirectory "$($process.Id)-hide-show-input.png"
    Save-WindowScreenshot -WindowHandle $windowHandle -Path $visibilityScreenshot

    $currentState = Switch-State -WindowHandle $windowHandle -Contract $contract -Scale $scale -CurrentState $currentState -TargetState "idle"
    $currentState = Switch-State -WindowHandle $windowHandle -Contract $contract -Scale $scale -CurrentState $currentState -TargetState "composer"
    [System.Windows.Forms.SendKeys]::SendWait("S")
    Start-Sleep -Milliseconds 150
    $stateRoundTripScreenshot = Join-Path $resolvedEvidenceDirectory "$($process.Id)-state-round-trip-input.png"
    Save-WindowScreenshot -WindowHandle $windowHandle -Path $stateRoundTripScreenshot

    Set-WindowKeyboardLayout -WindowHandle $windowHandle -LayoutId "00000804"
    foreach ($character in "yinghua".ToCharArray()) {
        [System.Windows.Forms.SendKeys]::SendWait([string]$character)
        Start-Sleep -Milliseconds 70
    }
    $imeScreenshot = Join-Path $resolvedEvidenceDirectory "$($process.Id)-ime-candidate.png"
    Save-WindowScreenshot -WindowHandle $windowHandle -Path $imeScreenshot
    [System.Windows.Forms.SendKeys]::SendWait(" ")
    Start-Sleep -Milliseconds 250
    $imeCommittedScreenshot = Join-Path $resolvedEvidenceDirectory "$($process.Id)-ime-committed.png"
    Save-WindowScreenshot -WindowHandle $windowHandle -Path $imeCommittedScreenshot
    Set-WindowKeyboardLayout -WindowHandle $windowHandle -LayoutId "00000409"

    $descendants = @(Get-DescendantProcesses -RootPid $process.Id)
    $pythonDescendants = @($descendants | Where-Object { $_.Name -match "^python(w)?\.exe$" })
    if ($pythonDescendants.Count -ne 0) { throw "The Shell started a Python descendant." }
    $descendantIds = @($descendants | ForEach-Object { [int]$_.ProcessId })

    $layout = $contract.states.$currentState
    $offsetX = [int]$contract.viewport.portraitAnchor[0] - [int]$layout.portraitAnchor[0]
    $offsetY = [int]$contract.viewport.portraitAnchor[1] - [int]$layout.portraitAnchor[1]
    $closePoint = Convert-LogicalPoint -Bounds (Get-WindowBounds -WindowHandle $windowHandle) -Scale $scale `
        -X ($offsetX + [double]$layout.controlsRect[0] + 18 + 31 * 5) `
        -Y ($offsetY + [double]$layout.controlsRect[1] + 19)
    Click-Point -X $closePoint.X -Y $closePoint.Y
    if (-not $process.WaitForExit(5000)) { throw "The Shell did not exit within five seconds." }
    Start-Sleep -Milliseconds 500
    $lingering = @($descendantIds | Where-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue })
    if ($lingering.Count -ne 0) { throw "Shell/WebView descendants remained after exit: $($lingering -join ', ')." }

    $dataAfter = Get-DataManifestHash -Root $DataRoot
    if ($dataBefore -ne $dataAfter) { throw "The real data/ manifest changed during acceptance." }

    [pscustomobject]@{
        Executable = $resolvedExecutable
        ExitCode = $process.ExitCode
        Dpi = $dpi
        ScaleFactor = $scale
        Screens = @([System.Windows.Forms.Screen]::AllScreens | ForEach-Object {
            [pscustomobject]@{
                DeviceName = $_.DeviceName
                Primary = $_.Primary
                Bounds = $_.Bounds.ToString()
                WorkingArea = $_.WorkingArea.ToString()
            }
        })
        HitOwnership = [pscustomobject]@{
            Transparent = "passed_to_background"
            BackgroundReceiverActivated = $true
            Portrait = "owned"
            Bubble = "owned"
            Controls = "owned"
            Input = "owned"
            Send = "owned"
        }
        Drag = [pscustomobject]@{
            BubbleBefore = $beforeBubbleDrag
            BubbleAfter = $afterBubbleDrag
            BubbleStateSwitchPreservedPosition = $true
            Before = $beforeDrag
            After = $afterDrag
            PortraitMovedWindow = $true
            InteractiveRegionsMovedWindow = $false
            AnchorStates = @($anchorStates)
        }
        Focus = [pscustomobject]@{
            EnglishKeys = "focus"
            AltTabLeftWindow = $leftForAltTab
            EnglishScreenshot = $englishScreenshot
            AltTabScreenshot = $altTabScreenshot
            HideShowScreenshot = $visibilityScreenshot
            StateRoundTripScreenshot = $stateRoundTripScreenshot
        }
        Ime = [pscustomobject]@{
            Layout = "Microsoft Pinyin / 00000804"
            CandidateScreenshot = $imeScreenshot
            CommittedScreenshot = $imeCommittedScreenshot
        }
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
