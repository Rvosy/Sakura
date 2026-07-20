param(
    [string]$TestExecutable = "",
    [string]$EvidenceDirectory = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;

public static class SakuraFakeCoreAcceptanceNative {
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

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern IntPtr CreateToolhelp32Snapshot(uint flags, uint processId);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool Process32First(IntPtr snapshot, ref PROCESSENTRY32 entry);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool Process32Next(IntPtr snapshot, ref PROCESSENTRY32 entry);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool CloseHandle(IntPtr handle);

    public static int[] GetDescendantProcessIds(int rootPid) {
        var descendants = new List<int>();
        var pending = new Queue<int>();
        pending.Enqueue(rootPid);
        while (pending.Count > 0) {
            var parentPid = pending.Dequeue();
            var snapshot = CreateToolhelp32Snapshot(2, 0);
            if (snapshot == new IntPtr(-1)) continue;
            try {
                var entry = new PROCESSENTRY32();
                entry.dwSize = (uint)Marshal.SizeOf<PROCESSENTRY32>();
                if (!Process32First(snapshot, ref entry)) continue;
                do {
                    if (entry.th32ParentProcessID == (uint)parentPid) {
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
$DepsRoot = (Resolve-Path -LiteralPath (Join-Path $RepoRoot "desktop\src-tauri\target\debug\deps")).Path
$TempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd('\')
if (-not $TestExecutable) {
    $candidate = Get-ChildItem -LiteralPath $DepsRoot -Filter "sakura_runtime_v2_shell-*.exe" -File |
        Sort-Object LastWriteTimeUtc -Descending |
        Select-Object -First 1
    if ($null -eq $candidate) { throw "No built Rust test executable was found under $DepsRoot." }
    $TestExecutable = $candidate.FullName
}
$TestExecutable = (Resolve-Path -LiteralPath $TestExecutable).Path
if (-not $TestExecutable.StartsWith($DepsRoot + "\", [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Test executable must remain under target/debug/deps."
}
if ([System.IO.Path]::GetFileName($TestExecutable) -notmatch '^sakura_runtime_v2_shell-[0-9a-f]+\.exe$') {
    throw "Unexpected Rust test executable name: $TestExecutable"
}

$binarySourceInputs = @(
    Get-Item -LiteralPath (Join-Path $RepoRoot "desktop\src-tauri\src\fake_core_runtime.rs")
    Get-Item -LiteralPath (Join-Path $RepoRoot "desktop\src-tauri\src\core_supervisor.rs")
    Get-Item -LiteralPath (Join-Path $RepoRoot "desktop\src-tauri\src\managed_process_tree.rs")
    Get-Item -LiteralPath (Join-Path $RepoRoot "desktop\src-tauri\src\main.rs")
)
$evidenceInputs = @($binarySourceInputs) + @(Get-Item -LiteralPath $MyInvocation.MyCommand.Path)
$sourceNewest = $binarySourceInputs | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
$testExecutableInfo = Get-Item -LiteralPath $TestExecutable
if ($testExecutableInfo.LastWriteTimeUtc -lt $sourceNewest.LastWriteTimeUtc) {
    throw "Rust test executable is older than WP-1B-03 source; rebuild before acceptance."
}

if (-not $EvidenceDirectory) {
    $EvidenceDirectory = Join-Path $RepoRoot (
        "temp\runtime-v2-wp-1b-03\acceptance-" + (Get-Date -Format "yyyyMMdd-HHmmss")
    )
}
$Evidence = [System.IO.Path]::GetFullPath($EvidenceDirectory)
[System.IO.Directory]::CreateDirectory($Evidence) | Out-Null
$StdoutPath = Join-Path $Evidence "stdout.log"
$StderrPath = Join-Path $Evidence "stderr.log"
$tracked = [System.Collections.Generic.Dictionary[string, object]]::new()

function Get-MatchingAcceptanceProcesses {
    return @(Get-Process -ErrorAction SilentlyContinue | Where-Object {
        try { [System.StringComparer]::OrdinalIgnoreCase.Equals($_.Path, $TestExecutable) }
        catch { $false }
    })
}

function Get-RootFixtureDirectories {
    param([int]$RootProcessId)
    if ($RootProcessId -le 0) { return @() }
    return @(Get-ChildItem -LiteralPath $TempRoot -Directory -Filter (
        "sakura-runtime-v2-wp-1b-03-$RootProcessId-*"
    ) -ErrorAction SilentlyContinue)
}

function Register-Identity {
    param([System.Diagnostics.Process]$Process)
    if ($null -eq $Process) { return }
    try {
        $startTicks = $Process.StartTime.ToUniversalTime().Ticks
        $path = $Process.Path
    }
    catch { return }
    $key = "$($Process.Id):$startTicks"
    if (-not $tracked.ContainsKey($key)) {
        $tracked[$key] = [pscustomobject]@{ Id = $Process.Id; StartTicks = $startTicks; Path = $path }
    }
}

function Test-IdentityAlive {
    param([object]$Identity)
    $process = Get-Process -Id $Identity.Id -ErrorAction SilentlyContinue
    if ($null -eq $process) { return $false }
    try {
        return $process.StartTime.ToUniversalTime().Ticks -eq $Identity.StartTicks -and
            [System.StringComparer]::OrdinalIgnoreCase.Equals($process.Path, $Identity.Path)
    }
    catch { return $false }
}

function Observe-Tree {
    param([System.Diagnostics.Process]$Root)
    Register-Identity -Process $Root
    if ($Root.HasExited) { return }
    foreach ($processId in @([SakuraFakeCoreAcceptanceNative]::GetDescendantProcessIds($Root.Id))) {
        $child = Get-Process -Id $processId -ErrorAction SilentlyContinue
        if ($null -ne $child) { Register-Identity -Process $child }
    }
}

function Stop-TrackedProcesses {
    foreach ($identity in @($tracked.Values)) {
        if (Test-IdentityAlive -Identity $identity) {
            Stop-Process -Id $identity.Id -Force -ErrorAction SilentlyContinue
        }
    }
    $deadline = [DateTime]::UtcNow.AddSeconds(5)
    do {
        $living = @($tracked.Values | Where-Object { Test-IdentityAlive -Identity $_ })
        if ($living.Count -eq 0) { return }
        Start-Sleep -Milliseconds 50
    } while ([DateTime]::UtcNow -lt $deadline)
}

$existing = @(Get-MatchingAcceptanceProcesses)
if ($existing.Count -ne 0) { throw "The WP-1B-03 test executable is already running." }
$staleFixtures = @(Get-ChildItem -LiteralPath $TempRoot -Directory `
    -Filter "sakura-runtime-v2-wp-1b-03-*" -ErrorAction SilentlyContinue)
if ($staleFixtures.Count -ne 0) {
    throw "Pre-existing WP-1B-03 fixture directories make acceptance ambiguous: $($staleFixtures.FullName -join ', ')."
}

$process = $null
$acceptanceRootId = 0
try {
    $process = Start-Process -FilePath $TestExecutable `
        -ArgumentList @("fake_core_runtime::tests::", "--nocapture", "--test-threads=1") `
        -WorkingDirectory $Evidence -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput $StdoutPath -RedirectStandardError $StderrPath
    $acceptanceRootId = $process.Id
    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    do {
        Observe-Tree -Root $process
        if ($process.HasExited) { break }
        Start-Sleep -Milliseconds 20
    } while ([DateTime]::UtcNow -lt $deadline)
    if (-not $process.HasExited) { throw "WP-1B-03 acceptance exceeded 30 seconds." }
    if ($process.ExitCode -ne 0) { throw "WP-1B-03 acceptance exited $($process.ExitCode)." }
}
finally {
    if ($null -ne $process) { Observe-Tree -Root $process }
    foreach ($matchingProcess in @(Get-MatchingAcceptanceProcesses)) {
        Register-Identity -Process $matchingProcess
    }
    if (@($tracked.Values | Where-Object { Test-IdentityAlive -Identity $_ }).Count -ne 0) {
        Stop-TrackedProcesses
    }
    $remaining = @(Get-MatchingAcceptanceProcesses)
    if ($remaining.Count -ne 0) {
        foreach ($remainingProcess in $remaining) { Register-Identity -Process $remainingProcess }
        Stop-TrackedProcesses
        $remaining = @(Get-MatchingAcceptanceProcesses)
    }

    foreach ($directory in @(Get-RootFixtureDirectories -RootProcessId $acceptanceRootId)) {
        $resolved = [System.IO.Path]::GetFullPath($directory.FullName)
        if (-not $resolved.StartsWith($TempRoot + "\", [System.StringComparison]::OrdinalIgnoreCase) -or
            $directory.Name -notmatch "^sakura-runtime-v2-wp-1b-03-$acceptanceRootId-[0-9]+$") {
            throw "Refusing unsafe Fake Core fixture cleanup target: $resolved"
        }
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
    $trackedRemaining = @($tracked.Values | Where-Object { Test-IdentityAlive -Identity $_ })
    $fixtureRemaining = @(Get-RootFixtureDirectories -RootProcessId $acceptanceRootId)
    if ($remaining.Count -ne 0 -or $trackedRemaining.Count -ne 0 -or $fixtureRemaining.Count -ne 0) {
        throw "Fake Core acceptance left process or fixture residuals."
    }
}

$stdout = Get-Content -LiteralPath $StdoutPath -Raw
$expectedTests = @(
    "delayed_hello_can_complete_before_deadline_and_then_shutdown_normally",
    "delayed_hello_wait_does_not_block_app_shutdown_or_tree_reclamation",
    "fake_core_ignoring_shutdown_is_forced_and_the_tree_is_reclaimed",
    "marker_observed_at_an_expired_deadline_is_rejected",
    "normal_fake_core_hello_and_protocol_shutdown_finalize_once"
)
foreach ($testName in $expectedTests) {
    if ($stdout -notmatch [regex]::Escape($testName)) {
        throw "Acceptance output lacked $testName."
    }
}
if ($stdout -notmatch 'test result: ok\. 5 passed; 0 failed; 3 ignored;') {
    throw "Acceptance output did not contain the expected 5/5 result."
}

$summary = [pscustomobject]@{
    status = "passed"
    testExecutable = $TestExecutable
    testExecutableSha256 = (Get-FileHash -LiteralPath $TestExecutable -Algorithm SHA256).Hash.ToLowerInvariant()
    sourceManifest = @($evidenceInputs | Sort-Object FullName | ForEach-Object {
        [pscustomobject]@{
            path = $_.FullName
            length = $_.Length
            mtimeUtc = $_.LastWriteTimeUtc.ToString("O")
            sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    })
    deadlineSeconds = 30
    helloDeadlineSeconds = 3
    shutdownDeadlineSeconds = 3
    fullStopDeadlineSeconds = 5
    requiredScenarios = $expectedTests.Count
    trackedProcessIdentities = $tracked.Count
    processIdentitiesRemaining = 0
    fixtureDirectoriesRemaining = 0
    dataScope = "WP-1B-03 acceptance does not access the repository data directory"
    normalProtocolShutdownValidated = $true
    ignoredShutdownForcedValidated = $true
    delayedHelloResponsiveShutdownValidated = $true
    idempotentFinalizeValidated = $true
}
$summary | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $Evidence "summary.json") -Encoding utf8
$summary
