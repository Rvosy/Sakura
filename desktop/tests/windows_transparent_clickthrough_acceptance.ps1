param(
    [string]$Executable = "",
    [string]$EvidenceDirectory = "temp\harness\windows-transparent-clickthrough",
    [switch]$Build
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ($env:OS -ne "Windows_NT") {
    throw "The transparent click-through acceptance requires Windows."
}

$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..")).Path
$manifestPath = Join-Path $repoRoot "desktop\src-tauri\Cargo.toml"
$defaultExecutable = Join-Path $repoRoot "desktop\src-tauri\target\debug\sakura.exe"
$resolvedEvidenceDirectory = [System.IO.Path]::GetFullPath(
    $EvidenceDirectory,
    $repoRoot
)
[System.IO.Directory]::CreateDirectory($resolvedEvidenceDirectory) | Out-Null

if ($Build) {
    $harnessTargetDirectory = Join-Path $resolvedEvidenceDirectory "cargo-target"
    $previousCargoTargetDirectory = $env:CARGO_TARGET_DIR
    try {
        $env:CARGO_TARGET_DIR = $harnessTargetDirectory
        & cargo build --manifest-path $manifestPath --locked
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to build the Runtime v2 desktop shell."
        }
    }
    finally {
        $env:CARGO_TARGET_DIR = $previousCargoTargetDirectory
    }
    if (-not $Executable) {
        $Executable = Join-Path $harnessTargetDirectory "debug\sakura.exe"
    }
}

if (-not $Executable) {
    $Executable = $defaultExecutable
}
$resolvedExecutable = (Resolve-Path -LiteralPath $Executable).Path

Add-Type -AssemblyName System.Drawing.Common
Add-Type -AssemblyName System.Windows.Forms
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;

public static class SakuraTransparentClickthroughNative {
    private delegate bool EnumWindowsProc(IntPtr window, IntPtr parameter);

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
    public static extern uint GetDpiForWindow(IntPtr window);

    [DllImport("user32.dll")]
    public static extern int GetWindowRgnBox(IntPtr window, out RECT rect);

    [DllImport("gdi32.dll")]
    public static extern IntPtr CreateRectRgn(int left, int top, int right, int bottom);

    [DllImport("gdi32.dll")]
    public static extern bool PtInRegion(IntPtr region, int x, int y);

    [DllImport("gdi32.dll")]
    public static extern bool DeleteObject(IntPtr value);

    [DllImport("user32.dll")]
    public static extern int GetWindowRgn(IntPtr window, IntPtr region);

    [DllImport("user32.dll")]
    public static extern int GetWindowLong(IntPtr window, int index);

    [DllImport("user32.dll")]
    public static extern bool IsWindowVisible(IntPtr window);

    [DllImport("user32.dll")]
    private static extern bool EnumWindows(EnumWindowsProc callback, IntPtr parameter);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int GetWindowText(IntPtr window, StringBuilder text, int capacity);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int GetClassName(IntPtr window, StringBuilder text, int capacity);

    [DllImport("user32.dll")]
    public static extern IntPtr WindowFromPoint(POINT point);

    [DllImport("user32.dll")]
    public static extern IntPtr GetAncestor(IntPtr window, uint flags);

    [DllImport("user32.dll")]
    public static extern uint GetWindowThreadProcessId(IntPtr window, out uint processId);

    [DllImport("user32.dll")]
    public static extern bool SetWindowPos(
        IntPtr window,
        IntPtr insertAfter,
        int x,
        int y,
        int width,
        int height,
        uint flags
    );

    [DllImport("user32.dll")]
    public static extern bool SetForegroundWindow(IntPtr window);

    [DllImport("user32.dll")]
    public static extern IntPtr GetForegroundWindow();

    [DllImport("user32.dll")]
    public static extern bool SetCursorPos(int x, int y);

    [DllImport("user32.dll")]
    public static extern void mouse_event(uint flags, uint x, uint y, uint data, UIntPtr extra);

    public static IntPtr FindVisibleTopLevelWindow(uint processId, string title) {
        IntPtr result = IntPtr.Zero;
        EnumWindows(delegate(IntPtr window, IntPtr parameter) {
            uint ownerProcessId;
            GetWindowThreadProcessId(window, out ownerProcessId);
            if (ownerProcessId != processId || !IsWindowVisible(window)) {
                return true;
            }
            StringBuilder text = new StringBuilder(256);
            GetWindowText(window, text, text.Capacity);
            StringBuilder className = new StringBuilder(256);
            GetClassName(window, className, className.Capacity);
            if (text.ToString() == title || className.ToString() == "Window Class") {
                result = window;
                return false;
            }
            return true;
        }, IntPtr.Zero);
        return result;
    }
}
'@

function Wait-ForMainWindow {
    param([System.Diagnostics.Process]$Process)

    $deadline = [DateTime]::UtcNow.AddSeconds(15)
    do {
        if ($Process.HasExited) {
            throw "Process exited before its main window became visible."
        }
        $handle = [SakuraTransparentClickthroughNative]::FindVisibleTopLevelWindow(
            [uint32]$Process.Id,
            "Sakura Runtime v2"
        )
        if ($handle -ne [IntPtr]::Zero -and
            [SakuraTransparentClickthroughNative]::IsWindowVisible($handle)) {
            return $handle
        }
        Start-Sleep -Milliseconds 50
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Timed out waiting for a visible main window."
}

function Get-WindowBounds {
    param([IntPtr]$WindowHandle)

    $rect = [SakuraTransparentClickthroughNative+RECT]::new()
    if (-not [SakuraTransparentClickthroughNative]::GetWindowRect($WindowHandle, [ref]$rect)) {
        throw "GetWindowRect failed."
    }
    return [pscustomobject]@{
        X = $rect.Left
        Y = $rect.Top
        Width = $rect.Right - $rect.Left
        Height = $rect.Bottom - $rect.Top
    }
}

function Get-RootWindowAtPoint {
    param([int]$X, [int]$Y)

    $point = [SakuraTransparentClickthroughNative+POINT]::new()
    $point.X = $X
    $point.Y = $Y
    $hit = [SakuraTransparentClickthroughNative]::WindowFromPoint($point)
    if ($hit -eq [IntPtr]::Zero) {
        return [IntPtr]::Zero
    }
    return [SakuraTransparentClickthroughNative]::GetAncestor($hit, 2)
}

function Get-WindowProcessId {
    param([IntPtr]$WindowHandle)

    $processId = [uint32]0
    [void][SakuraTransparentClickthroughNative]::GetWindowThreadProcessId(
        $WindowHandle,
        [ref]$processId
    )
    return [int]$processId
}

function Click-Point {
    param([int]$X, [int]$Y)

    [void][SakuraTransparentClickthroughNative]::SetCursorPos($X, $Y)
    Start-Sleep -Milliseconds 50
    [SakuraTransparentClickthroughNative]::mouse_event(0x0002, 0, 0, 0, [UIntPtr]::Zero)
    [SakuraTransparentClickthroughNative]::mouse_event(0x0004, 0, 0, 0, [UIntPtr]::Zero)
}

function Drag-Point {
    param([int]$X, [int]$Y, [int]$DeltaX, [int]$DeltaY)

    [void][SakuraTransparentClickthroughNative]::SetCursorPos($X, $Y)
    Start-Sleep -Milliseconds 80
    [SakuraTransparentClickthroughNative]::mouse_event(0x0002, 0, 0, 0, [UIntPtr]::Zero)
    Start-Sleep -Milliseconds 100
    for ($step = 1; $step -le 5; $step++) {
        [void][SakuraTransparentClickthroughNative]::SetCursorPos(
            $X + [int]($DeltaX * $step / 5),
            $Y + [int]($DeltaY * $step / 5)
        )
        Start-Sleep -Milliseconds 30
    }
    [SakuraTransparentClickthroughNative]::mouse_event(0x0004, 0, 0, 0, [UIntPtr]::Zero)
    Start-Sleep -Milliseconds 180
}

function Get-RegionCandidatePoints {
    param(
        [IntPtr]$Region,
        [object]$Bounds,
        [bool]$Inside,
        [int]$Limit = 100
    )

    $points = [System.Collections.Generic.List[object]]::new()
    $stepX = [Math]::Max(2, [int][Math]::Floor($Bounds.Width / 80))
    $stepY = [Math]::Max(2, [int][Math]::Floor($Bounds.Height / 80))
    $margin = [Math]::Max(4, [int][Math]::Ceiling($stepX / 2))
    for ($localY = $Bounds.Height - $margin; $localY -ge $margin; $localY -= $stepY) {
        for ($localX = $margin; $localX -lt $Bounds.Width - $margin; $localX += $stepX) {
            $contains = [SakuraTransparentClickthroughNative]::PtInRegion(
                $Region,
                $localX,
                $localY
            )
            if ($contains -eq $Inside) {
                $points.Add([pscustomobject]@{
                    LocalX = $localX
                    LocalY = $localY
                    X = $Bounds.X + $localX
                    Y = $Bounds.Y + $localY
                })
                if ($points.Count -ge $Limit) {
                    return $points
                }
            }
        }
    }
    return $points
}

$receiver = $null
$pet = $null
$region = [IntPtr]::Zero
$petStdoutPath = Join-Path $resolvedEvidenceDirectory "pet-stdout.log"
$petStderrPath = Join-Path $resolvedEvidenceDirectory "pet-stderr.log"
try {
    # Start WebView2 before creating the independent receiver window. On some
    # Windows/WebView2 builds, initializing another GUI message loop first can
    # make Tauri's setup-time monitor query lose its event-loop response.
    $petHandle = [IntPtr]::Zero
    for ($attempt = 1; $attempt -le 5 -and $petHandle -eq [IntPtr]::Zero; $attempt++) {
        $pet = Start-Process -FilePath $resolvedExecutable `
            -WorkingDirectory $repoRoot `
            -PassThru `
            -RedirectStandardOutput $petStdoutPath `
            -RedirectStandardError $petStderrPath `
            -WindowStyle Normal
        try {
            $petHandle = Wait-ForMainWindow -Process $pet
        }
        catch {
            if (-not $pet.HasExited) {
                $pet.Kill($true)
                $pet.WaitForExit()
            }
            if ($attempt -eq 5) {
                throw
            }
            Start-Sleep -Milliseconds 300
        }
    }
    Start-Sleep -Milliseconds 1200
    $petHandle = Wait-ForMainWindow -Process $pet

    $receiverHandlePath = Join-Path $resolvedEvidenceDirectory `
        "receiver-handle-$([Guid]::NewGuid().ToString('N')).txt"
    $receiverClickPath = Join-Path $resolvedEvidenceDirectory `
        "receiver-clicks-$([Guid]::NewGuid().ToString('N')).txt"
    $receiverCode = @'
Add-Type -AssemblyName System.Windows.Forms
$form = [System.Windows.Forms.Form]::new()
$form.Text = "Sakura Transparent Click Receiver"
$form.StartPosition = "Manual"
$form.FormBorderStyle = "None"
$form.SetBounds(0, 0, 2000, 1400)
$form.BackColor = [System.Drawing.Color]::FromArgb(80, 30, 100)
$clickCount = 0
$form.Add_MouseDown({
    $script:clickCount += 1
    [System.IO.File]::WriteAllText("__CLICK_PATH__", $script:clickCount.ToString())
})
$form.Show()
[System.IO.File]::WriteAllText("__HANDLE_PATH__", $form.Handle.ToInt64().ToString())
[System.IO.File]::WriteAllText("__CLICK_PATH__", "0")
[System.Windows.Forms.Application]::Run($form)
'@
    $receiverCode = $receiverCode.Replace("__HANDLE_PATH__", $receiverHandlePath)
    $receiverCode = $receiverCode.Replace("__CLICK_PATH__", $receiverClickPath)
    $encodedReceiver = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($receiverCode))
    $receiver = Start-Process -FilePath (Get-Command pwsh).Source `
        -ArgumentList "-NoProfile", "-EncodedCommand", $encodedReceiver `
        -PassThru `
        -WindowStyle Normal
    $receiverDeadline = [DateTime]::UtcNow.AddSeconds(15)
    $receiverHandle = [IntPtr]::Zero
    do {
        if ($receiver.HasExited) {
            throw "Independent receiver exited before publishing its window handle."
        }
        if (Test-Path -LiteralPath $receiverHandlePath) {
            $publishedHandle = 0L
            if ([long]::TryParse(
                (Get-Content -LiteralPath $receiverHandlePath -Raw),
                [ref]$publishedHandle
            )) {
                $receiverHandle = [IntPtr]$publishedHandle
            }
        }
        if ($receiverHandle -eq [IntPtr]::Zero -or
            -not [SakuraTransparentClickthroughNative]::IsWindowVisible($receiverHandle)) {
            Start-Sleep -Milliseconds 50
            $receiverHandle = [IntPtr]::Zero
        }
    } while ($receiverHandle -eq [IntPtr]::Zero -and [DateTime]::UtcNow -lt $receiverDeadline)
    if ($receiverHandle -eq [IntPtr]::Zero) {
        throw "Timed out waiting for the independent receiver form handle."
    }

    $bounds = Get-WindowBounds -WindowHandle $petHandle
    $dpi = [SakuraTransparentClickthroughNative]::GetDpiForWindow($petHandle)
    $scale = $dpi / 96.0
    $isolatedX = 50
    $isolatedY = 50
    if (-not [SakuraTransparentClickthroughNative]::SetWindowPos(
        $receiverHandle,
        [IntPtr](-1),
        $isolatedX,
        $isolatedY,
        $bounds.Width,
        $bounds.Height,
        0x0010
    )) {
        throw "Failed to place the independent receiver behind the pet."
    }
    if (-not [SakuraTransparentClickthroughNative]::SetWindowPos(
        $petHandle,
        [IntPtr](-1),
        $isolatedX,
        $isolatedY,
        $bounds.Width,
        $bounds.Height,
        0x0010
    )) {
        throw "Failed to keep the pet immediately above the independent receiver."
    }
    Start-Sleep -Milliseconds 300
    $bounds = Get-WindowBounds -WindowHandle $petHandle
    if (-not [SakuraTransparentClickthroughNative]::SetWindowPos(
        $receiverHandle,
        [IntPtr](-1),
        $bounds.X,
        $bounds.Y,
        $bounds.Width,
        $bounds.Height,
        0x0010
    )) {
        throw "Failed to align the independent receiver with the isolated pet window."
    }
    if (-not [SakuraTransparentClickthroughNative]::SetWindowPos(
        $petHandle,
        [IntPtr](-1),
        0,
        0,
        0,
        0,
        0x0013
    )) {
        throw "Failed to restore the isolated pet to the top of the test window pair."
    }

    $region = [SakuraTransparentClickthroughNative]::CreateRectRgn(0, 0, 0, 0)
    if ($region -eq [IntPtr]::Zero) {
        throw "Failed to allocate a region for dynamic surface inspection."
    }
    $regionComplexity = [SakuraTransparentClickthroughNative]::GetWindowRgn($petHandle, $region)
    if ($regionComplexity -le 1) {
        throw "The pet did not expose a precise non-empty native region."
    }
    $transparentCandidates = Get-RegionCandidatePoints -Region $region -Bounds $bounds -Inside $false -Limit 20
    $visibleCandidates = Get-RegionCandidatePoints -Region $region -Bounds $bounds -Inside $true -Limit 120
    if ($transparentCandidates.Count -eq 0) {
        throw "No transparent point exists inside the dynamic pet window; alpha holes may have regressed."
    }
    if ($visibleCandidates.Count -eq 0) {
        throw "No visible point exists inside the dynamic pet window."
    }
    $transparentPoint = $transparentCandidates[0]
    $portraitPoint = $visibleCandidates[0]
    $transparentX = $transparentPoint.X
    $transparentY = $transparentPoint.Y
    $portraitX = $portraitPoint.X
    $portraitY = $portraitPoint.Y

    [void][SakuraTransparentClickthroughNative]::SetCursorPos($transparentX, $transparentY)
    Start-Sleep -Milliseconds 100
    $transparentOwner = Get-RootWindowAtPoint -X $transparentX -Y $transparentY
    $transparentOwnerProcessId = Get-WindowProcessId -WindowHandle $transparentOwner
    if ($transparentOwner -eq [IntPtr]::Zero -or $transparentOwnerProcessId -eq $pet.Id) {
        $regionRect = [SakuraTransparentClickthroughNative+RECT]::new()
        $regionComplexity = [SakuraTransparentClickthroughNative]::GetWindowRgnBox(
            $petHandle,
            [ref]$regionRect
        )
        $extendedStyle = [uint32][SakuraTransparentClickthroughNative]::GetWindowLong($petHandle, -20)
        throw "Transparent point is still owned by the pet: owner=$($transparentOwner.ToInt64()), ownerPid=$transparentOwnerProcessId, pet=$($petHandle.ToInt64()), petPid=$($pet.Id), receiver=$($receiverHandle.ToInt64()), receiverPid=$($receiver.Id), regionComplexity=$regionComplexity, extendedStyle=0x$($extendedStyle.ToString('x8'))."
    }
    [void][SakuraTransparentClickthroughNative]::SetCursorPos($portraitX, $portraitY)
    Start-Sleep -Milliseconds 100
    $portraitOwner = Get-RootWindowAtPoint -X $portraitX -Y $portraitY
    $portraitOwnerProcessId = Get-WindowProcessId -WindowHandle $portraitOwner
    if ($portraitOwnerProcessId -ne $pet.Id) {
        $portraitExtendedStyle = [uint32][SakuraTransparentClickthroughNative]::GetWindowLong(
            $petHandle,
            -20
        )
        throw "Visible portrait point is not owned by the pet: owner=$($portraitOwner.ToInt64()), ownerPid=$portraitOwnerProcessId, petPid=$($pet.Id), extendedStyle=0x$($portraitExtendedStyle.ToString('x8'))."
    }

    for ($index = 0; $index -lt 20; $index++) {
        [void][SakuraTransparentClickthroughNative]::SetWindowPos(
            $petHandle,
            [IntPtr](-1),
            0,
            0,
            0,
            0,
            0x0013
        )
        Click-Point -X $transparentX -Y $transparentY
        Start-Sleep -Milliseconds 30
    }
    Start-Sleep -Milliseconds 250
    $receiverClicks = [int](Get-Content -LiteralPath $receiverClickPath -Raw)
    if ($receiverClicks -ne 20) {
        throw "Expected 20 transparent clicks in the background receiver, observed $receiverClicks."
    }

    for ($index = 0; $index -lt 20; $index++) {
        [void][SakuraTransparentClickthroughNative]::SetWindowPos(
            $petHandle,
            [IntPtr](-1),
            0,
            0,
            0,
            0,
            0x0013
        )
        Click-Point -X $portraitX -Y $portraitY
        Start-Sleep -Milliseconds 20
    }
    Start-Sleep -Milliseconds 150
    $receiverClicksAfterVisible = [int](Get-Content -LiteralPath $receiverClickPath -Raw)
    if ($receiverClicksAfterVisible -ne $receiverClicks) {
        throw "A native-visible pet point leaked a click to the background receiver."
    }

    [void][SakuraTransparentClickthroughNative]::SetWindowPos(
        $petHandle,
        [IntPtr](-1),
        0,
        0,
        0,
        0,
        0x0013
    )
    $beforeTransparentDrag = Get-WindowBounds -WindowHandle $petHandle
    Drag-Point -X $transparentX -Y $transparentY -DeltaX 28 -DeltaY 18
    $afterTransparentDrag = Get-WindowBounds -WindowHandle $petHandle
    if ($afterTransparentDrag.X -ne $beforeTransparentDrag.X -or
        $afterTransparentDrag.Y -ne $beforeTransparentDrag.Y) {
        throw "A transparent point unexpectedly started a pet window drag."
    }

    $dragPoint = $null
    foreach ($candidate in $visibleCandidates) {
        [void][SakuraTransparentClickthroughNative]::SetWindowPos(
            $petHandle,
            [IntPtr](-1),
            0,
            0,
            0,
            0,
            0x0013
        )
        $beforeDrag = Get-WindowBounds -WindowHandle $petHandle
        Drag-Point -X $candidate.X -Y $candidate.Y -DeltaX 24 -DeltaY 16
        $afterDrag = Get-WindowBounds -WindowHandle $petHandle
        if ($afterDrag.X -ne $beforeDrag.X -or $afterDrag.Y -ne $beforeDrag.Y) {
            $dragPoint = $candidate
            break
        }
    }
    if ($null -eq $dragPoint) {
        throw "No visible alpha point could start a pet window drag."
    }

    $beforeTopDrag = Get-WindowBounds -WindowHandle $petHandle
    $workingTop = [System.Windows.Forms.SystemInformation]::WorkingArea.Top
    $topDelta = $workingTop - $beforeTopDrag.Y
    Drag-Point `
        -X ($beforeTopDrag.X + $dragPoint.LocalX) `
        -Y ($beforeTopDrag.Y + $dragPoint.LocalY) `
        -DeltaX 0 `
        -DeltaY $topDelta
    $topBounds = Get-WindowBounds -WindowHandle $petHandle
    if ([Math]::Abs($topBounds.Y - $workingTop) -gt [Math]::Ceiling(2 * $scale)) {
        throw "The dynamic pet window could not reach the work-area top edge."
    }

    $evidence = [pscustomobject]@{
        Executable = $resolvedExecutable
        Dpi = $dpi
        ScaleFactor = $scale
        PetWindow = $bounds
        TransparentPoint = [pscustomobject]@{ X = $transparentX; Y = $transparentY }
        PortraitPoint = [pscustomobject]@{ X = $portraitX; Y = $portraitY }
        TransparentOwner = "background_process"
        TransparentOwnerProcessId = $transparentOwnerProcessId
        FallbackReceiverProcessId = $receiver.Id
        PortraitOwner = "pet"
        TransparentClicksDeliveredToBackground = $receiverClicks
        VisibleClicksRetainedByPet = 20
        TransparentPointRejectedDrag = $true
        VisibleAlphaPointStartedDrag = $true
        TopEdgeWindowY = $topBounds.Y
        WorkAreaTop = $workingTop
        NativeRegionComplexity = $regionComplexity
    }
    $reportPath = Join-Path $resolvedEvidenceDirectory "$($pet.Id)-transparent-clickthrough.json"
    $evidence | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $reportPath -Encoding utf8
    $evidence | ConvertTo-Json -Depth 5
}
catch {
    if ($null -ne $pet -and -not $pet.HasExited) {
        $pet.Kill($true)
        $pet.WaitForExit()
    }
    foreach ($logPath in @($petStdoutPath, $petStderrPath)) {
        if (Test-Path -LiteralPath $logPath) {
            Get-Content -LiteralPath $logPath | Write-Host
        }
    }
    throw
}
finally {
    if ($null -ne $region -and $region -ne [IntPtr]::Zero) {
        [void][SakuraTransparentClickthroughNative]::DeleteObject($region)
    }
    if ($null -ne $pet -and -not $pet.HasExited) {
        $pet.Kill($true)
        $pet.WaitForExit()
    }
    if ($null -ne $receiver -and -not $receiver.HasExited) {
        $receiver.Kill($true)
        $receiver.WaitForExit()
    }
}
