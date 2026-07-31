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
$defaultExecutable = Join-Path $repoRoot "desktop\src-tauri\target\debug\sakura-runtime-v2-shell.exe"
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
        $Executable = Join-Path $harnessTargetDirectory "debug\sakura-runtime-v2-shell.exe"
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

$receiver = $null
$pet = $null
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
    $receiverCode = @'
Add-Type -AssemblyName System.Windows.Forms
$form = [System.Windows.Forms.Form]::new()
$form.Text = "Sakura Transparent Click Receiver"
$form.StartPosition = "Manual"
$form.FormBorderStyle = "None"
$form.SetBounds(0, 0, 2000, 1400)
$form.BackColor = [System.Drawing.Color]::FromArgb(80, 30, 100)
$form.Show()
[System.IO.File]::WriteAllText("__HANDLE_PATH__", $form.Handle.ToInt64().ToString())
[System.Windows.Forms.Application]::Run($form)
'@
    $receiverCode = $receiverCode.Replace("__HANDLE_PATH__", $receiverHandlePath)
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

    $transparentX = $bounds.X + [int][Math]::Round(20 * $scale)
    $transparentY = $bounds.Y + [int][Math]::Round(20 * $scale)
    $portraitX = $bounds.X + [int][Math]::Round(480 * $scale)
    $portraitY = $bounds.Y + [int][Math]::Round(450 * $scale)

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

    [void][SakuraTransparentClickthroughNative]::SetForegroundWindow($petHandle)
    Start-Sleep -Milliseconds 100
    Click-Point -X $transparentX -Y $transparentY
    Start-Sleep -Milliseconds 250
    $foregroundProcessId = Get-WindowProcessId `
        -WindowHandle ([SakuraTransparentClickthroughNative]::GetForegroundWindow())
    if ($foregroundProcessId -ne $transparentOwnerProcessId) {
        throw "Transparent click did not activate the background process that owned the transparent point."
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
        TransparentClickActivatedBackground = $true
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
    if ($null -ne $pet -and -not $pet.HasExited) {
        $pet.Kill($true)
        $pet.WaitForExit()
    }
    if ($null -ne $receiver -and -not $receiver.HasExited) {
        $receiver.Kill($true)
        $receiver.WaitForExit()
    }
}
