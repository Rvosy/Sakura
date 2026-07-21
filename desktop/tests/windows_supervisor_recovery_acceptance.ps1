param(
    [string]$DebugExecutable = "",
    [string]$EvidenceDirectory = "",
    [string]$DataRoot = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;

public static class SakuraPhase1BRecoveryNative {
    public delegate bool EnumWindowsProc(IntPtr window, IntPtr parameter);

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public struct PROCESSENTRY32 {
        public uint dwSize;
        public uint cntUsage;
        public uint th32ProcessID;
        public IntPtr th32DefaultHeapID;
        public uint th32ModuleID;
        public int cntThreads;
        public uint th32ParentProcessID;
        public int pcPriClassBase;
        public uint dwFlags;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 260)]
        public string szExeFile;
    }

    [DllImport("user32.dll")]
    private static extern bool EnumWindows(EnumWindowsProc callback, IntPtr parameter);
    [DllImport("user32.dll")]
    private static extern uint GetWindowThreadProcessId(IntPtr window, out uint processId);
    [DllImport("user32.dll")]
    private static extern bool IsWindowVisible(IntPtr window);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int GetWindowText(IntPtr window, StringBuilder text, int capacity);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int GetClassName(IntPtr window, StringBuilder text, int capacity);
    [DllImport("user32.dll")]
    public static extern bool PostMessage(IntPtr window, uint message, UIntPtr wParam, IntPtr lParam);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern IntPtr CreateToolhelp32Snapshot(uint flags, uint processId);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool Process32First(IntPtr snapshot, ref PROCESSENTRY32 entry);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool Process32Next(IntPtr snapshot, ref PROCESSENTRY32 entry);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool CloseHandle(IntPtr handle);

    private static string ReadWindowText(IntPtr window) {
        var text = new StringBuilder(512);
        GetWindowText(window, text, text.Capacity);
        return text.ToString();
    }

    private static string ReadWindowClass(IntPtr window) {
        var text = new StringBuilder(256);
        GetClassName(window, text, text.Capacity);
        return text.ToString();
    }

    public static IntPtr FindVisibleWindow(int processId, string expectedTitle) {
        IntPtr result = IntPtr.Zero;
        EnumWindows((window, parameter) => {
            uint ownerProcessId;
            GetWindowThreadProcessId(window, out ownerProcessId);
            if (ownerProcessId == (uint)processId && IsWindowVisible(window) &&
                ReadWindowText(window) == expectedTitle) {
                result = window;
                return false;
            }
            return true;
        }, IntPtr.Zero);
        return result;
    }

    public static string[] DescribeVisibleWindows(int processId) {
        var result = new List<string>();
        EnumWindows((window, parameter) => {
            uint ownerProcessId;
            GetWindowThreadProcessId(window, out ownerProcessId);
            if (ownerProcessId == (uint)processId && IsWindowVisible(window)) {
                result.Add(window.ToInt64() + " | " + ReadWindowClass(window) + " | " + ReadWindowText(window));
            }
            return true;
        }, IntPtr.Zero);
        return result.ToArray();
    }

    public static int[] GetDescendantProcessIds(int rootPid) {
        var descendants = new List<int>();
        var pending = new Queue<int>();
        pending.Enqueue(rootPid);
        while (pending.Count > 0) {
            var parent = pending.Dequeue();
            var snapshot = CreateToolhelp32Snapshot(2, 0);
            if (snapshot == new IntPtr(-1)) continue;
            try {
                var entry = new PROCESSENTRY32();
                entry.dwSize = (uint)Marshal.SizeOf<PROCESSENTRY32>();
                if (!Process32First(snapshot, ref entry)) continue;
                do {
                    if (entry.th32ParentProcessID == (uint)parent) {
                        var child = (int)entry.th32ProcessID;
                        descendants.Add(child);
                        pending.Enqueue(child);
                    }
                    entry.dwSize = (uint)Marshal.SizeOf<PROCESSENTRY32>();
                } while (Process32Next(snapshot, ref entry));
            }
            finally { CloseHandle(snapshot); }
        }
        return descendants.ToArray();
    }
}
'@

$RepoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..")).Path
if (-not $DebugExecutable) {
    $DebugExecutable = Join-Path $RepoRoot "desktop\src-tauri\target\debug\sakura-runtime-v2-shell.exe"
}
$Debug = (Resolve-Path -LiteralPath $DebugExecutable).Path
if (-not $DataRoot) { $DataRoot = Join-Path $RepoRoot "data" }
$Data = (Resolve-Path -LiteralPath $DataRoot).Path
if (-not $EvidenceDirectory) {
    $EvidenceDirectory = Join-Path $RepoRoot (
        "temp\runtime-v2-wp-1b-04\acceptance-" + (Get-Date -Format "yyyyMMdd-HHmmss")
    )
}
$Evidence = [System.IO.Path]::GetFullPath($EvidenceDirectory)
[System.IO.Directory]::CreateDirectory($Evidence) | Out-Null
$TempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd('\')
$tracked = [System.Collections.Generic.Dictionary[string, object]]::new(
    [System.StringComparer]::OrdinalIgnoreCase
)
$runtimeDirectories = [System.Collections.Generic.List[string]]::new()
$ExpectedWindowTitle = "Sakura Runtime v2 · Pet interaction gate"

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
    [pscustomobject]@{
        records = $records
        count = $records.Count
        bytes = ($records | Measure-Object -Property length -Sum).Sum
        canonicalSha256 = [Convert]::ToHexString(
            [System.Security.Cryptography.SHA256]::HashData($bytes)
        ).ToLowerInvariant()
    }
}

function Register-Identity {
    param([System.Diagnostics.Process]$Process)
    if ($null -eq $Process) { return }
    try {
        $identity = [pscustomobject]@{
            id = $Process.Id
            startTicks = $Process.StartTime.ToUniversalTime().Ticks
            path = $Process.Path
        }
        $key = "$($identity.id):$($identity.startTicks)"
        if (-not $tracked.ContainsKey($key)) { $tracked[$key] = $identity }
    }
    catch { }
}

function Update-ProcessTree {
    param([System.Diagnostics.Process]$Root)
    Register-Identity -Process $Root
    if ($Root.HasExited) { return }
    foreach ($processId in [SakuraPhase1BRecoveryNative]::GetDescendantProcessIds($Root.Id)) {
        Register-Identity -Process (Get-Process -Id $processId -ErrorAction SilentlyContinue)
    }
}

function Test-IdentityAlive {
    param([object]$Identity)
    $process = Get-Process -Id $Identity.id -ErrorAction SilentlyContinue
    if ($null -eq $process) { return $false }
    try {
        return $process.StartTime.ToUniversalTime().Ticks -eq $Identity.startTicks -and
            [System.StringComparer]::OrdinalIgnoreCase.Equals($process.Path, $Identity.path)
    }
    catch { return $false }
}

function Wait-TrackedProcessesExit {
    param([int]$DeadlineMilliseconds)
    $deadline = [DateTime]::UtcNow.AddMilliseconds($DeadlineMilliseconds)
    do {
        $living = @($tracked.Values | Where-Object { Test-IdentityAlive -Identity $_ })
        if ($living.Count -eq 0) { return @() }
        Start-Sleep -Milliseconds 25
    } while ([DateTime]::UtcNow -lt $deadline)
    @($tracked.Values | Where-Object { Test-IdentityAlive -Identity $_ })
}

function Get-ExactExecutableProcesses {
    @(Get-Process -ErrorAction Stop | Where-Object {
        try {
            $_.Path -and [System.StringComparer]::OrdinalIgnoreCase.Equals($_.Path, $Debug)
        }
        catch { $false }
    })
}

function Wait-ScenarioReady {
    param(
        [System.Diagnostics.Process]$Process,
        [string]$Marker
    )
    $deadline = [DateTime]::UtcNow.AddSeconds(20)
    do {
        $Process.Refresh()
        Update-ProcessTree -Root $Process
        if ($Process.HasExited) {
            throw "Tauri acceptance exited before readiness (code $($Process.ExitCode))."
        }
        $window = [SakuraPhase1BRecoveryNative]::FindVisibleWindow(
            $Process.Id, $ExpectedWindowTitle
        )
        if ($window -ne [IntPtr]::Zero -and (Test-Path -LiteralPath $Marker)) {
            return $window
        }
        Start-Sleep -Milliseconds 25
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Tauri window and scenario marker were not both ready before deadline."
}

function Start-AcceptanceProcess {
    param([string]$Mode, [string]$RuntimeDirectory)
    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $Debug
    $startInfo.WorkingDirectory = $Evidence
    $startInfo.UseShellExecute = $false
    $startInfo.Environment["SAKURA_PHASE_1B_ACCEPTANCE_DIRECTORY"] = $RuntimeDirectory
    $startInfo.Environment["SAKURA_PHASE_1B_ACCEPTANCE_MODE"] = $Mode
    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    if (-not $process.Start()) { throw "Failed to start Tauri acceptance process." }
    Register-Identity -Process $process
    $process
}

function Stop-TrackedProcesses {
    foreach ($process in @(Get-ExactExecutableProcesses)) { Register-Identity -Process $process }
    foreach ($identity in @($tracked.Values)) {
        if (Test-IdentityAlive -Identity $identity) {
            Stop-Process -Id $identity.id -Force -ErrorAction SilentlyContinue
        }
    }
    $deadline = [DateTime]::UtcNow.AddSeconds(5)
    do {
        $living = @($tracked.Values | Where-Object { Test-IdentityAlive -Identity $_ })
        if ($living.Count -eq 0) { break }
        Start-Sleep -Milliseconds 50
    } while ([DateTime]::UtcNow -lt $deadline)
    $exact = @(Get-ExactExecutableProcesses)
    if ($living.Count -ne 0 -or $exact.Count -ne 0) {
        throw "Phase 1B acceptance left tracked or exact-path processes."
    }
}

function Invoke-Scenario {
    param([string]$Mode, [string]$ReadyMarker)
    $runtime = Join-Path $TempRoot (
        "sakura-runtime-v2-wp-1b-04-$PID-$Mode-" + [Guid]::NewGuid().ToString("N")
    )
    [System.IO.Directory]::CreateDirectory($runtime) | Out-Null
    $runtimeDirectories.Add($runtime)
    $process = $null
    try {
        $process = Start-AcceptanceProcess -Mode $Mode -RuntimeDirectory $runtime
        $window = Wait-ScenarioReady -Process $process -Marker (Join-Path $runtime $ReadyMarker)
        Update-ProcessTree -Root $process
        if (-not [SakuraPhase1BRecoveryNative]::PostMessage(
            $window, 0x0010, [UIntPtr]::Zero, [IntPtr]::Zero
        )) { throw "Failed to post WM_CLOSE to the Tauri window." }
        $deadline = [DateTime]::UtcNow.AddSeconds(10)
        do {
            $process.Refresh()
            Update-ProcessTree -Root $process
            if ($process.HasExited) { break }
            Start-Sleep -Milliseconds 25
        } while ([DateTime]::UtcNow -lt $deadline)
        if (-not $process.HasExited) {
            $timeoutEvidence = [pscustomobject]@{
                mode = $Mode
                rootPid = $process.Id
                windowHandle = $window.ToInt64()
                visibleWindows = @(
                    [SakuraPhase1BRecoveryNative]::DescribeVisibleWindows($process.Id)
                )
                cleaned = Test-Path -LiteralPath (Join-Path $runtime "acceptance.cleaned")
                workerError = if (Test-Path -LiteralPath (Join-Path $runtime "acceptance.error")) {
                    Get-Content -LiteralPath (Join-Path $runtime "acceptance.error") -Raw
                } else { $null }
                markers = @(Get-ChildItem -LiteralPath $runtime -File -Recurse | ForEach-Object {
                    [System.IO.Path]::GetRelativePath($runtime, $_.FullName).Replace("\", "/")
                } | Sort-Object)
            }
            $timeoutEvidence | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (
                Join-Path $Evidence "$Mode-timeout.json"
            ) -Encoding utf8
            throw "Tauri root did not exit before deadline; timeout evidence was preserved."
        }
        if ($process.ExitCode -ne 0) { throw "Tauri root exited with code $($process.ExitCode)." }
        if (-not (Test-Path -LiteralPath (Join-Path $runtime "acceptance.cleaned"))) {
            throw "Acceptance worker did not publish its cleanup marker."
        }
        if (Test-Path -LiteralPath (Join-Path $runtime "acceptance.error")) {
            throw (Get-Content -LiteralPath (Join-Path $runtime "acceptance.error") -Raw)
        }
        $living = @(Wait-TrackedProcessesExit -DeadlineMilliseconds 5000)
        if ($living.Count -ne 0) {
            $living | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (
                Join-Path $Evidence "$Mode-residual-processes.json"
            ) -Encoding utf8
            throw "Scenario retained tracked descendants after the 5 second deadline."
        }
        [pscustomobject]@{
            mode = $Mode
            windowVisible = $true
            rootExitCode = $process.ExitCode
            readyMarker = $ReadyMarker
            timerCancelled = Test-Path -LiteralPath (Join-Path $runtime "acceptance.timer_cancelled")
        }
    }
    finally {
        if ($null -ne $process -and -not $process.HasExited) {
            Update-ProcessTree -Root $process
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
            $process.WaitForExit(5000) | Out-Null
        }
        Stop-TrackedProcesses
        $resolved = [System.IO.Path]::GetFullPath($runtime)
        if (-not $resolved.StartsWith($TempRoot + "\", [System.StringComparison]::OrdinalIgnoreCase) -or
            [System.IO.Path]::GetFileName($resolved) -notmatch '^sakura-runtime-v2-wp-1b-04-[0-9]+-(pending-hello|restart-backoff)-[0-9a-f]{32}$') {
            throw "Refusing unsafe acceptance directory cleanup: $resolved"
        }
        if (Test-Path -LiteralPath $resolved) { Remove-Item -LiteralPath $resolved -Recurse -Force }
    }
}

$sourceInputs = @(
    Get-Item -LiteralPath (Join-Path $RepoRoot "desktop\src-tauri\src\core_supervisor.rs")
    Get-Item -LiteralPath (Join-Path $RepoRoot "desktop\src-tauri\src\managed_process_tree.rs")
    Get-Item -LiteralPath (Join-Path $RepoRoot "desktop\src-tauri\src\phase_1b_runtime_acceptance.rs")
    Get-Item -LiteralPath (Join-Path $RepoRoot "desktop\src-tauri\src\main.rs")
    Get-Item -LiteralPath $PSCommandPath
)
$newestSource = $sourceInputs | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
$debugItem = Get-Item -LiteralPath $Debug
if ($debugItem.LastWriteTimeUtc -lt $newestSource.LastWriteTimeUtc) {
    throw "The debug Tauri executable is older than acceptance source $($newestSource.FullName)."
}
$preexisting = @(Get-ExactExecutableProcesses)
if ($preexisting.Count -ne 0) {
    throw "The debug Tauri executable is already running before WP-1B-04 acceptance."
}
$before = Get-DataManifest -Root $Data
$before | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $Evidence "data-before.json") -Encoding utf8
$scenarioResults = @()
$acceptanceFailure = $null
try {
    $scenarioResults += Invoke-Scenario -Mode "pending-hello" -ReadyMarker "acceptance.pending_hello"
    $scenarioResults += Invoke-Scenario -Mode "restart-backoff" -ReadyMarker "acceptance.restart_backoff"
}
catch { $acceptanceFailure = $_ }
finally {
    Stop-TrackedProcesses
}
$after = Get-DataManifest -Root $Data
$after | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $Evidence "data-after.json") -Encoding utf8
if ($before.canonicalSha256 -ne $after.canonicalSha256) {
    throw "The real data/ path/length/mtime/SHA-256 manifest changed during acceptance."
}
if ($null -ne $acceptanceFailure) { throw $acceptanceFailure }
$remainingDirectories = @($runtimeDirectories | Where-Object { Test-Path -LiteralPath $_ })
if ($remainingDirectories.Count -ne 0) { throw "Acceptance runtime directories remained." }

$summary = [pscustomobject]@{
    status = "passed"
    scenarios = $scenarioResults
    scenarioCount = $scenarioResults.Count
    debugExecutable = $Debug
    debugExecutableSha256 = (Get-FileHash -LiteralPath $Debug -Algorithm SHA256).Hash.ToLowerInvariant()
    sourceManifest = @($sourceInputs | Sort-Object FullName | ForEach-Object {
        [pscustomobject]@{
            path = $_.FullName
            length = $_.Length
            mtimeUtc = $_.LastWriteTimeUtc.ToString("O")
            sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    })
    deadlineSeconds = 60
    helloDeadlineSeconds = 3
    shutdownDeadlineSeconds = 3
    fullStopDeadlineSeconds = 5
    trackedProcessIdentities = $tracked.Count
    processIdentitiesRemaining = 0
    runtimeDirectoriesRemaining = 0
    dataCount = $before.count
    dataBytes = $before.bytes
    dataBeforeSha256 = $before.canonicalSha256
    dataAfterSha256 = $after.canonicalSha256
}
$summary | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $Evidence "summary.json") -Encoding utf8
$summary
