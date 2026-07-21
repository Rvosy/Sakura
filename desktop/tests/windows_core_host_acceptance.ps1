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

public static class SakuraPhase1CCoreHostNative {
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
$Python = (Resolve-Path -LiteralPath (Join-Path $RepoRoot "runtime\python.exe")).Path
if (-not $DataRoot) { $DataRoot = Join-Path $RepoRoot "data" }
$Data = (Resolve-Path -LiteralPath $DataRoot).Path
if (-not $EvidenceDirectory) {
    $EvidenceDirectory = Join-Path $RepoRoot (
        "temp\runtime-v2-wp-1c-01\acceptance-" + (Get-Date -Format "yyyyMMdd-HHmmss")
    )
}
$Evidence = [System.IO.Path]::GetFullPath($EvidenceDirectory)
[System.IO.Directory]::CreateDirectory($Evidence) | Out-Null
$TempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd('\')
$ExpectedWindowTitle = "Sakura Runtime v2 · Pet interaction gate"
$tracked = [System.Collections.Generic.Dictionary[string, object]]::new(
    [System.StringComparer]::OrdinalIgnoreCase
)
$runtime = Join-Path $TempRoot (
    "sakura-runtime-v2-wp-1c-01-$PID-" + [Guid]::NewGuid().ToString("N")
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
    [pscustomobject]@{
        records = $records
        count = $records.Count
        bytes = ($records | Measure-Object -Property length -Sum).Sum
        canonicalSha256 = [Convert]::ToHexString(
            [System.Security.Cryptography.SHA256]::HashData(
                [System.Text.Encoding]::UTF8.GetBytes($canonical)
            )
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

function Update-ProcessTree {
    param([System.Diagnostics.Process]$Root)
    Register-Identity -Process $Root
    if ($Root.HasExited) { return }
    foreach ($processId in [SakuraPhase1CCoreHostNative]::GetDescendantProcessIds($Root.Id)) {
        Register-Identity -Process (Get-Process -Id $processId -ErrorAction SilentlyContinue)
    }
}

function Wait-TrackedExit {
    param([int]$DeadlineMilliseconds)
    $deadline = [DateTime]::UtcNow.AddMilliseconds($DeadlineMilliseconds)
    do {
        $living = @($tracked.Values | Where-Object { Test-IdentityAlive -Identity $_ })
        if ($living.Count -eq 0) { return @() }
        Start-Sleep -Milliseconds 25
    } while ([DateTime]::UtcNow -lt $deadline)
    @($tracked.Values | Where-Object { Test-IdentityAlive -Identity $_ })
}

function Stop-TrackedProcesses {
    foreach ($identity in @($tracked.Values)) {
        if (Test-IdentityAlive -Identity $identity) {
            Stop-Process -Id $identity.id -Force -ErrorAction SilentlyContinue
        }
    }
    $living = @(Wait-TrackedExit -DeadlineMilliseconds 5000)
    if ($living.Count -ne 0) { throw "WP-1C-01 retained tracked processes after cleanup." }
}

function Get-ExactDebugProcesses {
    @(Get-Process -ErrorAction Stop | Where-Object {
        try {
            $_.Path -and [System.StringComparer]::OrdinalIgnoreCase.Equals($_.Path, $Debug)
        }
        catch { $false }
    })
}

$sourceFiles = @(
    Get-Item -LiteralPath (Join-Path $RepoRoot "app\core_host\__main__.py")
    Get-Item -LiteralPath (Join-Path $RepoRoot "app\core_host\protocol.py")
    Get-Item -LiteralPath (Join-Path $RepoRoot "app\core_host\server.py")
    Get-Item -LiteralPath (Join-Path $RepoRoot "desktop\src-tauri\src\core_host_protocol.rs")
    Get-Item -LiteralPath (Join-Path $RepoRoot "desktop\src-tauri\src\core_host_runtime.rs")
    Get-Item -LiteralPath (Join-Path $RepoRoot "desktop\src-tauri\src\managed_process_tree.rs")
    Get-Item -LiteralPath (Join-Path $RepoRoot "desktop\src-tauri\src\main.rs")
    Get-Item -LiteralPath (Join-Path $RepoRoot "desktop\src-tauri\src\phase_1c_core_host_acceptance.rs")
    Get-Item -LiteralPath $PSCommandPath
)
$debugItem = Get-Item -LiteralPath $Debug
if (($sourceFiles | Measure-Object -Property LastWriteTimeUtc -Maximum).Maximum -gt $debugItem.LastWriteTimeUtc) {
    throw "The debug Tauri executable is older than WP-1C-01 acceptance source."
}
if (@(Get-ExactDebugProcesses).Count -ne 0) {
    throw "The debug Tauri executable is already running before WP-1C-01 acceptance."
}

[System.IO.Directory]::CreateDirectory($runtime) | Out-Null
$before = Get-DataManifest -Root $Data
$before | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (
    Join-Path $Evidence "data-before.json"
) -Encoding utf8
$process = $null
$failure = $null
$windowVisible = $false
try {
    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $Debug
    $startInfo.WorkingDirectory = $Evidence
    $startInfo.UseShellExecute = $false
    $startInfo.Environment["SAKURA_PHASE_1C_ACCEPTANCE_DIRECTORY"] = $runtime
    $startInfo.Environment["SAKURA_PHASE_1C_PYTHON"] = $Python
    $startInfo.Environment["SAKURA_PHASE_1C_REPO_ROOT"] = $RepoRoot
    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    if (-not $process.Start()) { throw "Failed to start WP-1C-01 Tauri acceptance." }
    Register-Identity -Process $process

    $readyDeadline = [DateTime]::UtcNow.AddSeconds(20)
    $window = [IntPtr]::Zero
    do {
        $process.Refresh()
        Update-ProcessTree -Root $process
        if ($process.HasExited) {
            throw "WP-1C-01 Tauri exited before readiness (code $($process.ExitCode))."
        }
        $window = [SakuraPhase1CCoreHostNative]::FindVisibleWindow(
            $process.Id, $ExpectedWindowTitle
        )
        $windowVisible = $window -ne [IntPtr]::Zero
        if ($windowVisible -and (Test-Path -LiteralPath (Join-Path $runtime "acceptance.ready"))) {
            break
        }
        Start-Sleep -Milliseconds 25
    } while ([DateTime]::UtcNow -lt $readyDeadline)
    if (-not $windowVisible -or -not (Test-Path -LiteralPath (Join-Path $runtime "acceptance.ready"))) {
        throw "Tauri window and real Core Host were not ready before deadline."
    }
    Update-ProcessTree -Root $process
    $corePid = [int](Get-Content -LiteralPath (Join-Path $runtime "core.pid") -Raw)
    Register-Identity -Process (Get-Process -Id $corePid -ErrorAction Stop)

    if (-not [SakuraPhase1CCoreHostNative]::PostMessage(
        $window, 0x0010, [UIntPtr]::Zero, [IntPtr]::Zero
    )) { throw "Failed to post WM_CLOSE to the Tauri window." }
    $exitDeadline = [DateTime]::UtcNow.AddSeconds(10)
    do {
        $process.Refresh()
        Update-ProcessTree -Root $process
        if ($process.HasExited) { break }
        Start-Sleep -Milliseconds 25
    } while ([DateTime]::UtcNow -lt $exitDeadline)
    if (-not $process.HasExited) { throw "WP-1C-01 Tauri root did not exit before deadline." }
    if ($process.ExitCode -ne 0) { throw "WP-1C-01 Tauri exited with code $($process.ExitCode)." }
    if (-not (Test-Path -LiteralPath (Join-Path $runtime "acceptance.cleaned"))) {
        throw "Real Core Host worker did not publish its cleanup marker."
    }
    if (Test-Path -LiteralPath (Join-Path $runtime "acceptance.error")) {
        throw (Get-Content -LiteralPath (Join-Path $runtime "acceptance.error") -Raw)
    }
    $living = @(Wait-TrackedExit -DeadlineMilliseconds 5000)
    if ($living.Count -ne 0) {
        $living | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (
            Join-Path $Evidence "residual-processes.json"
        ) -Encoding utf8
        throw "WP-1C-01 retained tracked processes after the exit deadline."
    }
}
catch {
    $failure = $_
    $diagnostic = [pscustomobject]@{
        message = $_.Exception.Message
        windowVisible = $windowVisible
        tauriPid = if ($null -ne $process) { $process.Id } else { $null }
        tauriExited = if ($null -ne $process) { $process.HasExited } else { $null }
        corePid = if (Test-Path -LiteralPath (Join-Path $runtime "core.pid")) {
            Get-Content -LiteralPath (Join-Path $runtime "core.pid") -Raw
        } else { $null }
        workerError = if (Test-Path -LiteralPath (Join-Path $runtime "acceptance.error")) {
            Get-Content -LiteralPath (Join-Path $runtime "acceptance.error") -Raw
        } else { $null }
        markers = if (Test-Path -LiteralPath $runtime) {
            @(Get-ChildItem -LiteralPath $runtime -File -Recurse | ForEach-Object {
                [System.IO.Path]::GetRelativePath($runtime, $_.FullName).Replace("\", "/")
            } | Sort-Object)
        } else { @() }
        trackedIdentities = @($tracked.Values)
    }
    $diagnostic | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (
        Join-Path $Evidence "failure-diagnostic.json"
    ) -Encoding utf8
}
finally {
    if ($null -ne $process -and -not $process.HasExited) {
        Update-ProcessTree -Root $process
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        $process.WaitForExit(5000) | Out-Null
    }
    Stop-TrackedProcesses
    $resolvedRuntime = [System.IO.Path]::GetFullPath($runtime)
    if (-not $resolvedRuntime.StartsWith($TempRoot + "\", [System.StringComparison]::OrdinalIgnoreCase) -or
        [System.IO.Path]::GetFileName($resolvedRuntime) -notmatch '^sakura-runtime-v2-wp-1c-01-[0-9]+-[0-9a-f]{32}$') {
        throw "Refusing unsafe WP-1C-01 acceptance cleanup: $resolvedRuntime"
    }
    if (Test-Path -LiteralPath $resolvedRuntime) {
        Remove-Item -LiteralPath $resolvedRuntime -Recurse -Force
    }
}

$after = Get-DataManifest -Root $Data
$after | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (
    Join-Path $Evidence "data-after.json"
) -Encoding utf8
if ($before.canonicalSha256 -ne $after.canonicalSha256) {
    throw "The real data/ path/length/mtime/SHA-256 manifest changed during WP-1C-01 acceptance."
}
if ($null -ne $failure) { throw $failure }
if (@(Get-ExactDebugProcesses).Count -ne 0) {
    throw "WP-1C-01 left an exact-path Tauri process."
}
if (Test-Path -LiteralPath $runtime) { throw "WP-1C-01 runtime directory remained." }

$sourceManifest = @($sourceFiles | ForEach-Object {
    [pscustomobject]@{
        path = $_.FullName
        length = $_.Length
        mtimeUtc = $_.LastWriteTimeUtc.ToString("O")
        sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    }
})
$summary = [pscustomobject]@{
    status = "passed"
    windowVisible = $windowVisible
    rootExitCode = $process.ExitCode
    hello = $true
    repeatedHealth = 2
    protocolShutdown = $true
    debugExecutable = $Debug
    debugExecutableSha256 = (Get-FileHash -LiteralPath $Debug -Algorithm SHA256).Hash.ToLowerInvariant()
    pythonExecutable = $Python
    pythonExecutableSha256 = (Get-FileHash -LiteralPath $Python -Algorithm SHA256).Hash.ToLowerInvariant()
    sourceManifest = $sourceManifest
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
$summary | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (
    Join-Path $Evidence "summary.json"
) -Encoding utf8
$summary
