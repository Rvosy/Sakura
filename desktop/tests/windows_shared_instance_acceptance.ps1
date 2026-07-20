param(
    [Parameter(Mandatory = $true)]
    [string]$DebugExecutable,

    [Parameter(Mandatory = $true)]
    [string]$ReleaseExecutable,

    [Parameter(Mandatory = $true)]
    [string]$EvidenceDirectory,

    [Parameter(Mandatory = $true)]
    [string]$DataRoot
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;

public static class SakuraSharedInstanceNative {
    public delegate bool EnumWindowsProc(IntPtr window, IntPtr parameter);

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public struct PROCESSENTRY32 {
        public uint dwSize;
        public uint cntUsage;
        public uint th32ProcessID;
        public IntPtr th32DefaultHeapID;
        public uint th32ModuleID;
        public uint cntThreads;
        public uint th32ParentProcessID;
        public int pcPriClassBase;
        public uint dwFlags;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 260)]
        public string szExeFile;
    }

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    public static extern IntPtr FindWindow(string className, string windowName);

    [DllImport("user32.dll")]
    public static extern bool PostMessage(IntPtr window, uint message, UIntPtr wParam, IntPtr lParam);

    [DllImport("user32.dll")]
    private static extern bool EnumWindows(EnumWindowsProc callback, IntPtr parameter);

    [DllImport("user32.dll")]
    private static extern uint GetWindowThreadProcessId(IntPtr window, out uint processId);

    [DllImport("user32.dll")]
    private static extern bool IsWindowVisible(IntPtr window);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int GetClassName(IntPtr window, System.Text.StringBuilder className, int maxCount);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int GetWindowText(IntPtr window, System.Text.StringBuilder title, int maxCount);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern IntPtr CreateEvent(IntPtr attributes, bool manualReset, bool initialState, string name);

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool CloseHandle(IntPtr handle);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern IntPtr CreateToolhelp32Snapshot(uint flags, uint processId);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool Process32First(IntPtr snapshot, ref PROCESSENTRY32 entry);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool Process32Next(IntPtr snapshot, ref PROCESSENTRY32 entry);

    public static int[] GetChildProcessIds(int parentPid) {
        var children = new List<int>();
        var snapshot = CreateToolhelp32Snapshot(2, 0);
        if (snapshot == new IntPtr(-1)) return children.ToArray();
        try {
            var entry = new PROCESSENTRY32();
            entry.dwSize = (uint)Marshal.SizeOf<PROCESSENTRY32>();
            if (!Process32First(snapshot, ref entry)) return children.ToArray();
            do {
                if (entry.th32ParentProcessID == (uint)parentPid) children.Add((int)entry.th32ProcessID);
                entry.dwSize = (uint)Marshal.SizeOf<PROCESSENTRY32>();
            } while (Process32Next(snapshot, ref entry));
            return children.ToArray();
        }
        finally { CloseHandle(snapshot); }
    }

    public static int[] GetDescendantProcessIds(int rootPid) {
        var descendants = new List<int>();
        var pending = new Queue<int>();
        pending.Enqueue(rootPid);
        while (pending.Count > 0) {
            foreach (var child in GetChildProcessIds(pending.Dequeue())) {
                descendants.Add(child);
                pending.Enqueue(child);
            }
        }
        return descendants.ToArray();
    }

    private static IntPtr FindVisibleWindow(int processId, string expectedClass, string expectedTitle) {
        IntPtr result = IntPtr.Zero;
        EnumWindows((window, parameter) => {
            uint ownerProcessId;
            GetWindowThreadProcessId(window, out ownerProcessId);
            if (ownerProcessId != (uint)processId || !IsWindowVisible(window)) return true;
            var className = new System.Text.StringBuilder(256);
            GetClassName(window, className, className.Capacity);
            if (expectedClass != null && !String.Equals(className.ToString(), expectedClass, StringComparison.Ordinal)) return true;
            var title = new System.Text.StringBuilder(512);
            GetWindowText(window, title, title.Capacity);
            if (expectedTitle != null && !String.Equals(title.ToString(), expectedTitle, StringComparison.Ordinal)) return true;
            result = window;
            return false;
        }, IntPtr.Zero);
        return result;
    }

    public static IntPtr FindVisibleTauriWindow(int processId) {
        return FindVisibleWindow(processId, "Tauri Window", null);
    }

    public static IntPtr FindVisibleWindowWithTitle(int processId, string title) {
        return FindVisibleWindow(processId, null, title);
    }
}
'@

$MutexName = "Local\SakuraDesktop.SharedUserData.v1"
$AlreadyRunningTitle = "Sakura 已在运行"
$FatalTitle = "Sakura 启动失败"
$WmClose = 0x0010
$RepoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..")).Path
$Python = (Resolve-Path -LiteralPath (Join-Path $RepoRoot "runtime\python.exe")).Path
$Debug = (Resolve-Path -LiteralPath $DebugExecutable).Path
$Release = (Resolve-Path -LiteralPath $ReleaseExecutable).Path
$Data = (Resolve-Path -LiteralPath $DataRoot).Path
$Evidence = [System.IO.Path]::GetFullPath($EvidenceDirectory)
[System.IO.Directory]::CreateDirectory($Evidence) | Out-Null
$startedRoots = [System.Collections.Generic.List[System.Diagnostics.Process]]::new()
$trackedProcessIdentities = [System.Collections.Generic.Dictionary[string, object]]::new(
    [System.StringComparer]::OrdinalIgnoreCase
)

function Get-DataManifest {
    param([string]$Root)

    $records = @(Get-ChildItem -LiteralPath $Root -File -Recurse | ForEach-Object {
        [pscustomobject]@{
            path = [System.IO.Path]::GetRelativePath($Root, $_.FullName).Replace("\", "/")
            length = $_.Length
            mtimeUtc = $_.LastWriteTimeUtc.ToString("O")
            sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    } | Sort-Object path)
    $canonical = ($records | ForEach-Object {
        "$($_.path)`t$($_.length)`t$($_.mtimeUtc)`t$($_.sha256)"
    }) -join "`n"
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($canonical)
    return [pscustomobject]@{
        records = $records
        count = $records.Count
        bytes = ($records | Measure-Object -Property length -Sum).Sum
        canonicalSha256 = [Convert]::ToHexString(
            [System.Security.Cryptography.SHA256]::HashData($bytes)
        ).ToLowerInvariant()
    }
}

function Get-DescendantIds {
    param([int]$RootPid)

    $root = Get-Process -Id $RootPid -ErrorAction SilentlyContinue
    if ($null -eq $root) { return @() }
    $rootStart = $root.StartTime
    return @([SakuraSharedInstanceNative]::GetDescendantProcessIds($RootPid) | Where-Object {
        $candidate = Get-Process -Id $_ -ErrorAction SilentlyContinue
        $null -ne $candidate -and $candidate.StartTime -ge $rootStart
    })
}

function Wait-ForProcessIdsExit {
    param(
        [int[]]$ProcessIds,
        [DateTime]$Deadline,
        [string]$Context
    )

    do {
        $living = @($ProcessIds | Where-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue })
        if ($living.Count -eq 0) { return }
        Start-Sleep -Milliseconds 50
    } while ([DateTime]::UtcNow -lt $Deadline)
    throw "$Context remained after deadline: $($living -join ', ')."
}

function Register-StartedRoot {
    param([System.Diagnostics.Process]$Process)

    [void]$startedRoots.Add($Process)
    Update-TrackedProcessTree -Process $Process
    return $Process
}

function Register-ProcessIdentity {
    param([System.Diagnostics.Process]$Process)

    if ($null -eq $Process) { return }
    try {
        $startTicks = $Process.StartTime.ToUniversalTime().Ticks
        $processPath = $Process.Path
    }
    catch { return }
    $key = "$($Process.Id):$startTicks"
    if (-not $trackedProcessIdentities.ContainsKey($key)) {
        $trackedProcessIdentities[$key] = [pscustomobject]@{
            Id = $Process.Id
            StartTicks = $startTicks
            Path = $processPath
        }
    }
}

function Update-TrackedProcessTree {
    param([System.Diagnostics.Process]$Process)

    Register-ProcessIdentity -Process $Process
    if ($null -eq $Process -or $Process.HasExited) { return }
    foreach ($processId in @(Get-DescendantIds -RootPid $Process.Id)) {
        $descendant = Get-Process -Id $processId -ErrorAction SilentlyContinue
        if ($null -ne $descendant) { Register-ProcessIdentity -Process $descendant }
    }
}

function Test-TrackedProcessIdentityAlive {
    param([object]$Identity)

    $process = Get-Process -Id $Identity.Id -ErrorAction SilentlyContinue
    if ($null -eq $process) { return $false }
    try {
        if ($process.StartTime.ToUniversalTime().Ticks -ne $Identity.StartTicks) { return $false }
        return [System.StringComparer]::OrdinalIgnoreCase.Equals($process.Path, $Identity.Path)
    }
    catch { return $false }
}

function Get-LivingTrackedProcessIdentities {
    return @($trackedProcessIdentities.Values | Where-Object {
        Test-TrackedProcessIdentityAlive -Identity $_
    })
}

function Stop-TrackedProcessTree {
    param([System.Diagnostics.Process]$Process)

    Update-TrackedProcessTree -Process $Process
    foreach ($identity in @(Get-LivingTrackedProcessIdentities | Sort-Object Id -Descending)) {
        if (Test-TrackedProcessIdentityAlive -Identity $identity) {
            Stop-Process -Id $identity.Id -Force -ErrorAction SilentlyContinue
        }
    }
    $deadline = [DateTime]::UtcNow.AddSeconds(5)
    do {
        $living = @(Get-LivingTrackedProcessIdentities)
        if ($living.Count -eq 0) { return }
        Start-Sleep -Milliseconds 50
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Tracked process tree remained after deadline: $($living.Id -join ', ')."
}

function Wait-ForWindow {
    param(
        [System.Diagnostics.Process]$Process,
        [string]$Title = "",
        [int]$Seconds = 12,
        [string]$Context = "window acceptance"
    )

    $deadline = [DateTime]::UtcNow.AddSeconds($Seconds)
    do {
        Update-TrackedProcessTree -Process $Process
        if ($Process.HasExited) {
            throw "$Context`: process $($Process.Id) exited before its window appeared (code $($Process.ExitCode))."
        }
        if ($Title) {
            $handle = [SakuraSharedInstanceNative]::FindVisibleWindowWithTitle($Process.Id, $Title)
        }
        else {
            $handle = [SakuraSharedInstanceNative]::FindVisibleTauriWindow($Process.Id)
        }
        if ($handle -ne [IntPtr]::Zero) { return $handle }
        Start-Sleep -Milliseconds 50
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "$Context`: timed out waiting for process $($Process.Id) window '$Title'."
}

function Close-WindowAndAssertExit {
    param(
        [System.Diagnostics.Process]$Process,
        [IntPtr]$Window,
        [int]$ExpectedExitCode,
        [int[]]$DescendantIds = @(),
        [switch]$SkipExitCodeCheck
    )

    $deadline = [DateTime]::UtcNow.AddSeconds(8)
    Update-TrackedProcessTree -Process $Process
    if (-not [SakuraSharedInstanceNative]::PostMessage($Window, $WmClose, [UIntPtr]::Zero, [IntPtr]::Zero)) {
        throw "WM_CLOSE failed for process $($Process.Id)."
    }
    $remainingMs = [Math]::Max(1, [int]($deadline - [DateTime]::UtcNow).TotalMilliseconds)
    if (-not $Process.WaitForExit($remainingMs)) { throw "Process $($Process.Id) did not exit before deadline." }
    if (-not $SkipExitCodeCheck -and $Process.ExitCode -ne $ExpectedExitCode) {
        throw "Process $($Process.Id) exit code $($Process.ExitCode), expected $ExpectedExitCode."
    }
    Wait-ForProcessIdsExit -ProcessIds $DescendantIds -Deadline $deadline -Context "Descendants"
}

function Start-Tauri {
    param([string]$Executable)

    return Register-StartedRoot (Start-Process -FilePath $Executable `
        -WorkingDirectory $RepoRoot -PassThru -WindowStyle Normal)
}

function Invoke-TauriSuccess {
    param([string]$Executable, [string]$Context = "Tauri success")

    $process = Start-Tauri -Executable $Executable
    $window = Wait-ForWindow -Process $process -Context $Context
    $descendants = @(Get-DescendantIds -RootPid $process.Id)
    $pythonChildren = @($descendants | ForEach-Object {
        $candidate = Get-Process -Id $_ -ErrorAction SilentlyContinue
        if ($null -ne $candidate -and $candidate.ProcessName -match '^pythonw?$') { $candidate }
    })
    if ($pythonChildren.Count -ne 0) { throw "Tauri started a Python descendant." }
    Close-WindowAndAssertExit -Process $process -Window $window -ExpectedExitCode 0 -DescendantIds $descendants
}

function Invoke-ExpectedDialogExit {
    param(
        [string]$FilePath,
        [string[]]$ArgumentList,
        [string]$Title,
        [int]$ExpectedExitCode,
        [string]$WorkingDirectory = $RepoRoot,
        [string]$Context = "expected startup dialog"
    )

    $process = Register-StartedRoot (Start-Process -FilePath $FilePath -ArgumentList $ArgumentList `
        -WorkingDirectory $WorkingDirectory -PassThru -WindowStyle Normal)
    $window = Wait-ForWindow -Process $process -Title $Title -Context $Context
    $descendants = @(Get-DescendantIds -RootPid $process.Id)
    Close-WindowAndAssertExit -Process $process -Window $window `
        -ExpectedExitCode $ExpectedExitCode -DescendantIds $descendants
}

function Wait-ForReadyFile {
    param([System.Diagnostics.Process]$Process, [string]$ReadyFile)

    $deadline = [DateTime]::UtcNow.AddSeconds(15)
    do {
        Update-TrackedProcessTree -Process $Process
        if (Test-Path -LiteralPath $ReadyFile) { return }
        if ($Process.HasExited) { throw "Qt smoke exited before readiness (code $($Process.ExitCode))." }
        Start-Sleep -Milliseconds 50
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Timed out waiting for Qt smoke readiness."
}

function New-IsolatedQtRoot {
    param([string]$Name)

    $root = Join-Path $Evidence $Name
    [System.IO.Directory]::CreateDirectory($root) | Out-Null
    Copy-Item -LiteralPath (Join-Path $RepoRoot "tests\fixtures\runtime_v2\wp_0_02\dataset\data") `
        -Destination $root -Recurse
    Copy-Item -LiteralPath (Join-Path $RepoRoot "tests\fixtures\runtime_v2\wp_0_02\dataset\characters") `
        -Destination $root -Recurse
    $portrait = Join-Path $root "characters\fixture\portraits\fixture.ico"
    Copy-Item -LiteralPath (Join-Path $RepoRoot "desktop\src-tauri\icons\icon.ico") -Destination $portrait
    $manifestPath = Join-Path $root "characters\fixture\character.json"
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    $manifest.portrait.default = "portraits/fixture.ico"
    $manifest.portrait.expressions.neutral = "portraits/fixture.ico"
    $manifest | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $manifestPath -Encoding utf8
    Set-Content -LiteralPath (Join-Path $root "data\sakura.lock") -Value "stale fixture only" -Encoding utf8
    return $root
}

function Start-QtSmoke {
    param(
        [string]$Name,
        [int]$HoldMs,
        [int]$DrainHoldMs = 0,
        [switch]$DrainFail
    )

    $root = New-IsolatedQtRoot -Name $Name
    $ready = Join-Path $root "ready.txt"
    $drainReady = Join-Path $root "drain-ready.txt"
    $helper = Join-Path $RepoRoot "tests\fixtures\runtime_v2\wp_1a_04\legacy_qt_smoke.py"
    $arguments = @(
        $helper, "--base-dir", $root, "--ready-file", $ready, "--hold-ms", "$HoldMs"
    )
    if ($DrainHoldMs -gt 0) {
        $arguments += @("--drain-ready-file", $drainReady, "--drain-hold-ms", "$DrainHoldMs")
    }
    if ($DrainFail) {
        $arguments += @("--drain-ready-file", $drainReady, "--drain-fail")
    }
    $process = Register-StartedRoot (Start-Process -FilePath $Python -ArgumentList $arguments `
        -WorkingDirectory $RepoRoot -PassThru -WindowStyle Hidden)
    Wait-ForReadyFile -Process $process -ReadyFile $ready
    return [pscustomobject]@{
        Process = $process
        Root = $root
        Ready = $ready
        DrainReady = $drainReady
    }
}

function Wait-ForCleanExit {
    param(
        [System.Diagnostics.Process]$Process,
        [int[]]$DescendantIds = @(),
        [int]$ExpectedExitCode = 0
    )

    $deadline = [DateTime]::UtcNow.AddSeconds(15)
    do {
        Update-TrackedProcessTree -Process $Process
        if ($Process.HasExited) { break }
        Start-Sleep -Milliseconds 50
    } while ([DateTime]::UtcNow -lt $deadline)
    if (-not $Process.HasExited) { throw "Process $($Process.Id) did not exit before deadline." }
    if ($Process.ExitCode -ne $ExpectedExitCode) {
        throw "Process $($Process.Id) exit code $($Process.ExitCode), expected $ExpectedExitCode."
    }
    Wait-ForProcessIdsExit -ProcessIds $DescendantIds -Deadline $deadline -Context "Qt descendants"
}

function Wait-ForChildProcess {
    param([System.Diagnostics.Process]$Parent, [string]$NamePattern)

    $deadline = [DateTime]::UtcNow.AddSeconds(12)
    do {
        Update-TrackedProcessTree -Process $Parent
        if ($Parent.HasExited) { throw "Parent $($Parent.Id) exited before child '$NamePattern' appeared." }
        $children = @([SakuraSharedInstanceNative]::GetChildProcessIds($Parent.Id) | ForEach-Object {
            Get-Process -Id $_ -ErrorAction SilentlyContinue
        } | Where-Object { $_.ProcessName -match $NamePattern })
        if ($children.Count -gt 0) { return $children[0] }
        Start-Sleep -Milliseconds 50
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Timed out waiting for child '$NamePattern'."
}

function Wait-ForNewProcess {
    param(
        [string]$ProcessName,
        [int[]]$KnownProcessIds = @(),
        [string]$Context = "new process"
    )

    $deadline = [DateTime]::UtcNow.AddSeconds(12)
    do {
        $candidate = Get-Process -Name $ProcessName -ErrorAction SilentlyContinue | Where-Object {
            $KnownProcessIds -notcontains $_.Id
        } | Select-Object -First 1
        if ($null -ne $candidate) { return $candidate }
        Start-Sleep -Milliseconds 50
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "$Context`: timed out waiting for a new '$ProcessName' process."
}

$existingShells = @(Get-Process -Name "sakura-runtime-v2-shell" -ErrorAction SilentlyContinue)
if ($existingShells.Count -ne 0) { throw "A Sakura Runtime v2 shell is already running before acceptance." }
$existingShellIds = @($existingShells | ForEach-Object { $_.Id })

$before = Get-DataManifest -Root $Data
$before | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $Evidence "data-before.json") -Encoding utf8
$after = $null
$scenarios = [System.Collections.Generic.List[string]]::new()

try {
    foreach ($executable in @($Debug, $Release)) {
        Invoke-TauriSuccess -Executable $executable -Context "initial Tauri launch ($executable)"
        Invoke-TauriSuccess -Executable $executable -Context "repeated Tauri launch ($executable)"
        $scenarios.Add("tauri success/repeat/release: $executable")
    }

    $qt = Start-QtSmoke -Name "qt-holds-for-tauri-conflict" -HoldMs 5000
    Invoke-ExpectedDialogExit -FilePath $Release -ArgumentList @() `
        -Title $AlreadyRunningTitle -ExpectedExitCode 0 -Context "Tauri conflict while Qt holds"
    Wait-ForCleanExit -Process $qt.Process -DescendantIds @(Get-DescendantIds -RootPid $qt.Process.Id)
    $scenarios.Add("real isolated Qt holds -> release Tauri already_running")

    $tauri = Start-Tauri -Executable $Release
    $tauriWindow = Wait-ForWindow -Process $tauri -Context "Tauri holder for legacy Qt conflict"
    Invoke-ExpectedDialogExit -FilePath $Python -ArgumentList @("legacy_qt_main.py") `
        -Title $AlreadyRunningTitle -ExpectedExitCode 0 -Context "legacy Qt conflict while Tauri holds"
    $tauriDescendants = @(Get-DescendantIds -RootPid $tauri.Id)
    Close-WindowAndAssertExit -Process $tauri -Window $tauriWindow -ExpectedExitCode 0 `
        -DescendantIds $tauriDescendants
    $scenarios.Add("release Tauri holds -> real legacy Qt already_running")

    $event = [SakuraSharedInstanceNative]::CreateEvent([IntPtr]::Zero, $true, $false, $MutexName)
    if ($event -eq [IntPtr]::Zero) { throw "Failed to create same-name Event fatal injector." }
    try {
        Invoke-ExpectedDialogExit -FilePath $Debug -ArgumentList @() -Title $FatalTitle `
            -ExpectedExitCode 1 -Context "Tauri same-name Event fatal"
        Invoke-ExpectedDialogExit -FilePath $Python -ArgumentList @("legacy_qt_main.py") `
            -Title $FatalTitle -ExpectedExitCode 1 -Context "legacy Qt same-name Event fatal"
    }
    finally {
        if (-not [SakuraSharedInstanceNative]::CloseHandle($event)) { throw "Failed to close fatal injector Event." }
    }
    $scenarios.Add("real Win32 API fatal for Tauri and Qt")

    $tauri = Start-Tauri -Executable $Debug
    [void](Wait-ForWindow -Process $tauri -Context "Tauri holder before strong kill")
    $tauriDescendants = @(Get-DescendantIds -RootPid $tauri.Id)
    Stop-Process -Id $tauri.Id -Force
    $tauri.WaitForExit()
    Wait-ForProcessIdsExit -ProcessIds $tauriDescendants `
        -Deadline ([DateTime]::UtcNow.AddSeconds(5)) -Context "Tauri descendants after strong kill"
    $qt = Start-QtSmoke -Name "qt-after-tauri-strong-kill" -HoldMs 800
    Wait-ForCleanExit -Process $qt.Process -DescendantIds @(Get-DescendantIds -RootPid $qt.Process.Id)
    $scenarios.Add("Tauri strong kill releases -> Qt acquires")

    $qt = Start-QtSmoke -Name "qt-strong-kill" -HoldMs 60000
    $qtDescendants = @(Get-DescendantIds -RootPid $qt.Process.Id)
    Stop-Process -Id $qt.Process.Id -Force
    $qt.Process.WaitForExit()
    Wait-ForProcessIdsExit -ProcessIds $qtDescendants `
        -Deadline ([DateTime]::UtcNow.AddSeconds(5)) -Context "Qt descendants after strong kill"
    Invoke-TauriSuccess -Executable $Release -Context "Tauri after Qt strong kill"
    $scenarios.Add("Qt strong kill releases -> Tauri acquires")

    $qt = Start-QtSmoke -Name "qt-normal-release-and-stale-lock" -HoldMs 800
    Wait-ForCleanExit -Process $qt.Process -DescendantIds @(Get-DescendantIds -RootPid $qt.Process.Id)
    Invoke-TauriSuccess -Executable $Debug -Context "Tauri after Qt normal release and stale file lock"
    $scenarios.Add("Qt normal release and stale data/sakura.lock ignored")

    $qt = Start-QtSmoke -Name "qt-qthread-drain-holds-shared-lock" -HoldMs 100 -DrainHoldMs 8000
    Wait-ForReadyFile -Process $qt.Process -ReadyFile $qt.DrainReady
    Invoke-ExpectedDialogExit -FilePath $Release -ArgumentList @() `
        -Title $AlreadyRunningTitle -ExpectedExitCode 0 -Context "Tauri conflict during Qt QThread drain"
    Wait-ForCleanExit -Process $qt.Process -DescendantIds @(Get-DescendantIds -RootPid $qt.Process.Id)
    Invoke-TauriSuccess -Executable $Debug -Context "Tauri after Qt QThread drain completed"
    $scenarios.Add("Qt QThread drain retains shared mutex until process exit")

    $qt = Start-QtSmoke -Name "qt-qthread-drain-timeout-fails-closed" -HoldMs 100 -DrainFail
    Wait-ForReadyFile -Process $qt.Process -ReadyFile $qt.DrainReady
    Wait-ForCleanExit -Process $qt.Process -DescendantIds @(Get-DescendantIds -RootPid $qt.Process.Id) `
        -ExpectedExitCode 1
    Invoke-TauriSuccess -Executable $Release -Context "Tauri after Qt QThread drain timeout"
    $scenarios.Add("Qt QThread drain timeout terminates process before mutex release")

    $knownShellIds = @(Get-Process -Name "sakura-runtime-v2-shell" -ErrorAction SilentlyContinue | ForEach-Object { $_.Id })
    $defaultPython = Register-StartedRoot (Start-Process -FilePath $Python -ArgumentList @("main.py") `
        -WorkingDirectory $RepoRoot -PassThru -WindowStyle Normal)
    $defaultShell = Register-StartedRoot (Wait-ForNewProcess -ProcessName "sakura-runtime-v2-shell" `
        -KnownProcessIds $knownShellIds -Context "default main.py Tauri handoff")
    if (-not $defaultPython.WaitForExit(8000) -or $defaultPython.ExitCode -ne 0) {
        throw "default main.py launcher did not hand off cleanly."
    }
    $defaultWindow = Wait-ForWindow -Process $defaultShell -Context "default main.py Tauri handoff"
    $defaultDescendants = @(Get-DescendantIds -RootPid $defaultShell.Id)
    $pythonChildren = @($defaultDescendants | ForEach-Object {
        $candidate = Get-Process -Id $_ -ErrorAction SilentlyContinue
        if ($null -ne $candidate -and $candidate.ProcessName -match '^pythonw?$') { $candidate }
    })
    if ($pythonChildren.Count -ne 0) { throw "Default main.py kept or spawned a Python lifecycle child." }
    Close-WindowAndAssertExit -Process $defaultShell -Window $defaultWindow -ExpectedExitCode 0 `
        -DescendantIds $defaultDescendants -SkipExitCodeCheck
    $scenarios.Add("default main.py hands off to Tauri without a resident Python lifecycle root")

    $knownShellIds = @(Get-Process -Name "sakura-runtime-v2-shell" -ErrorAction SilentlyContinue | ForEach-Object { $_.Id })
    $batch = Register-StartedRoot (Start-Process -FilePath "cmd.exe" -ArgumentList @("/d", "/c", "start.bat") `
        -WorkingDirectory $RepoRoot -PassThru -WindowStyle Hidden)
    $batchShell = Register-StartedRoot (Wait-ForNewProcess -ProcessName "sakura-runtime-v2-shell" `
        -KnownProcessIds $knownShellIds -Context "start.bat Tauri shell")
    $batchWindow = Wait-ForWindow -Process $batchShell -Context "start.bat Tauri shell"
    $batchDescendants = @(Get-DescendantIds -RootPid $batchShell.Id)
    Close-WindowAndAssertExit -Process $batchShell -Window $batchWindow -ExpectedExitCode 0 `
        -DescendantIds $batchDescendants -SkipExitCodeCheck
    if (-not $batch.WaitForExit(8000) -or $batch.ExitCode -ne 0) { throw "start.bat did not exit cleanly." }
    $scenarios.Add("start.bat directly runs Tauri")

    $tauri = Start-Tauri -Executable $Release
    $tauriWindow = Wait-ForWindow -Process $tauri -Context "Tauri holder for legacy batch conflict"
    $legacyBatch = Register-StartedRoot (Start-Process -FilePath "cmd.exe" -ArgumentList @("/d", "/c", "start-legacy-qt.bat") `
        -WorkingDirectory $RepoRoot -PassThru -WindowStyle Hidden)
    $legacyPython = Register-StartedRoot (Wait-ForChildProcess -Parent $legacyBatch -NamePattern '^python$')
    $legacyDialog = Wait-ForWindow -Process $legacyPython -Title $AlreadyRunningTitle `
        -Context "start-legacy-qt.bat conflict"
    Close-WindowAndAssertExit -Process $legacyPython -Window $legacyDialog `
        -ExpectedExitCode 0 -SkipExitCodeCheck
    if (-not $legacyBatch.WaitForExit(8000) -or $legacyBatch.ExitCode -ne 0) {
        throw "start-legacy-qt.bat did not return the expected conflict result."
    }
    Close-WindowAndAssertExit -Process $tauri -Window $tauriWindow -ExpectedExitCode 0 `
        -DescendantIds @(Get-DescendantIds -RootPid $tauri.Id)
    $scenarios.Add("start-legacy-qt.bat propagates shared-lock conflict")
}
finally {
    $cleanupErrors = [System.Collections.Generic.List[string]]::new()
    foreach ($startedRoot in @($startedRoots)) {
        try { Stop-TrackedProcessTree -Process $startedRoot }
        catch { $cleanupErrors.Add($_.Exception.Message) }
    }
    $remainingTracked = @(Get-LivingTrackedProcessIdentities)
    $remainingProjectPython = @($remainingTracked | Where-Object {
        [System.StringComparer]::OrdinalIgnoreCase.Equals($_.Path, $Python)
    })
    if ($remainingProjectPython.Count -ne 0) {
        $cleanupErrors.Add("Project runtime Python roots remained after acceptance: $($remainingProjectPython.Id -join ', ').")
    }
    if ($remainingTracked.Count -ne 0) {
        $cleanupErrors.Add("Tracked process identities remained after acceptance: $($remainingTracked.Id -join ', ').")
    }
    $after = Get-DataManifest -Root $Data
    $after | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $Evidence "data-after.json") -Encoding utf8
    if ($before.canonicalSha256 -ne $after.canonicalSha256) {
        throw "The real data/ path/length/UTC mtime/SHA-256 manifest changed during acceptance."
    }
    if ($cleanupErrors.Count -ne 0) {
        throw "Acceptance cleanup failed: $($cleanupErrors -join '; ')"
    }
}

$summary = [pscustomobject]@{
    status = "passed"
    scenarios = @($scenarios)
    scenarioCount = $scenarios.Count
    dataFiles = $before.count
    dataBytes = $before.bytes
    dataBeforeSha256 = $before.canonicalSha256
    dataAfterSha256 = $after.canonicalSha256
    dataUnchanged = $true
    rootProcessesRemaining = 0
    mutexName = $MutexName
}
$summary | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $Evidence "summary.json") -Encoding utf8
$summary
