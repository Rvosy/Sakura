param(
    [string]$TestExecutable = "",
    [string]$EvidenceDirectory = "",
    [switch]$FailureCleanupProbe
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;

public static class SakuraManagedTreeAcceptanceNative {
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
if (-not $TestExecutable) {
    $candidate = Get-ChildItem -LiteralPath $DepsRoot -Filter "sakura_runtime_v2_shell-*.exe" -File |
        Sort-Object LastWriteTimeUtc -Descending |
        Select-Object -First 1
    if ($null -eq $candidate) { throw "No built Rust test executable was found under $DepsRoot." }
    $TestExecutable = $candidate.FullName
}
$TestExecutable = (Resolve-Path -LiteralPath $TestExecutable).Path
if (-not $TestExecutable.StartsWith($DepsRoot + "\", [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Test executable must remain under the repository target/debug/deps directory."
}
if ([System.IO.Path]::GetFileName($TestExecutable) -notmatch '^sakura_runtime_v2_shell-[0-9a-f]+\.exe$') {
    throw "Unexpected Rust test executable name: $TestExecutable"
}
$sourceInputs = @(
    Get-Item -LiteralPath (Join-Path $RepoRoot "desktop\src-tauri\Cargo.toml")
    Get-Item -LiteralPath (Join-Path $RepoRoot "desktop\src-tauri\src\main.rs")
    Get-Item -LiteralPath (Join-Path $RepoRoot "desktop\src-tauri\src\managed_process_tree.rs")
)
$sourceNewest = $sourceInputs | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
$testExecutableInfo = Get-Item -LiteralPath $TestExecutable
if ($testExecutableInfo.LastWriteTimeUtc -lt $sourceNewest.LastWriteTimeUtc) {
    throw "Rust test executable is older than WP-1B-01 source $($sourceNewest.FullName); rebuild before acceptance."
}
$testExecutableSha256 = (Get-FileHash -LiteralPath $TestExecutable -Algorithm SHA256).Hash.ToLowerInvariant()
$sourceManifest = @($sourceInputs | Sort-Object FullName | ForEach-Object {
    [pscustomobject]@{
        path = $_.FullName
        length = $_.Length
        mtimeUtc = $_.LastWriteTimeUtc.ToString("O")
        sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    }
})

if (-not $EvidenceDirectory) {
    $EvidenceDirectory = Join-Path $RepoRoot (
        "temp\runtime-v2-wp-1b-01\acceptance-" + (Get-Date -Format "yyyyMMdd-HHmmss")
    )
}
$Evidence = [System.IO.Path]::GetFullPath($EvidenceDirectory)
[System.IO.Directory]::CreateDirectory($Evidence) | Out-Null
$StdoutPath = Join-Path $Evidence "stdout.log"
$StderrPath = Join-Path $Evidence "stderr.log"
$tracked = [System.Collections.Generic.Dictionary[string, object]]::new()

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
        $tracked[$key] = [pscustomobject]@{
            Id = $Process.Id
            StartTicks = $startTicks
            Path = $path
        }
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
    foreach ($processId in @([SakuraManagedTreeAcceptanceNative]::GetDescendantProcessIds($Root.Id))) {
        $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
        if ($null -ne $process) { Register-Identity -Process $process }
    }
}

function Stop-ObservedTree {
    foreach ($identity in @($tracked.Values | Sort-Object Id -Descending)) {
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
    throw "Observed acceptance process tree remained: $($living.Id -join ', ')."
}

function Get-MatchingAcceptanceProcesses {
    return @(Get-Process -ErrorAction SilentlyContinue | Where-Object {
        try { [System.StringComparer]::OrdinalIgnoreCase.Equals($_.Path, $TestExecutable) }
        catch { $false }
    })
}

$existing = @(Get-MatchingAcceptanceProcesses)
if ($existing.Count -ne 0) {
    throw "The WP-1B-01 Rust test executable is already running before acceptance."
}

$process = $null
try {
    $testArguments = @("managed_process_tree::tests::", "--nocapture", "--test-threads=1")
    if ($FailureCleanupProbe) {
        $testArguments = @(
            "--ignored",
            "--exact",
            "managed_process_tree::tests::fixture_root_exits_with_descendant_holding",
            "--nocapture",
            "--test-threads=1"
        )
    }
    $process = Start-Process -FilePath $TestExecutable `
        -ArgumentList $testArguments `
        -WorkingDirectory $RepoRoot -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput $StdoutPath -RedirectStandardError $StderrPath
    $deadline = [DateTime]::UtcNow.AddSeconds(45)
    do {
        Observe-Tree -Root $process
        if ($process.HasExited) { break }
        Start-Sleep -Milliseconds 20
    } while ([DateTime]::UtcNow -lt $deadline)
    if (-not $process.HasExited) {
        Observe-Tree -Root $process
        Stop-ObservedTree
        throw "WP-1B-01 real process-tree acceptance exceeded its 45 second deadline."
    }
    if ($process.ExitCode -ne 0) {
        throw "WP-1B-01 Rust acceptance exited with code $($process.ExitCode)."
    }
}
finally {
    if ($null -ne $process) {
        Observe-Tree -Root $process
    }
    foreach ($matchingProcess in @(Get-MatchingAcceptanceProcesses)) {
        Register-Identity -Process $matchingProcess
    }
    $living = @($tracked.Values | Where-Object { Test-IdentityAlive -Identity $_ })
    if ($living.Count -ne 0) { Stop-ObservedTree }

    $remaining = @(Get-MatchingAcceptanceProcesses)
    if ($remaining.Count -ne 0) {
        foreach ($remainingProcess in $remaining) { Register-Identity -Process $remainingProcess }
        Stop-ObservedTree
        $remaining = @(Get-MatchingAcceptanceProcesses)
    }
    if ($remaining.Count -ne 0) {
        throw "Rust test roots or descendants remained after acceptance: $($remaining.Id -join ', ')."
    }
}

$stdout = Get-Content -LiteralPath $StdoutPath -Raw
$expectedTests = @(
    "normal_root_exit_is_observed_and_releases_exited_handles_explicitly",
    "timeout_and_forced_tree_termination_have_distinct_explicit_semantics",
    "forced_termination_reclaims_one_level_and_multi_level_descendants",
    "root_exit_does_not_hide_a_surviving_descendant",
    "job_poll_rejects_an_empty_observation_made_after_the_deadline",
    "assignment_failure_terminates_the_suspended_unmanaged_process",
    "resume_failure_terminates_the_already_assigned_job_tree",
    "embedded_nul_in_program_or_arguments_is_rejected_before_win32_spawn",
    "windows_command_line_quoting_covers_spaces_quotes_and_trailing_backslashes",
    "caller_already_in_a_windows_job_can_create_an_independent_managed_tree",
    "drop_is_final_insurance_for_a_live_multi_level_tree",
    "spawn_failures_and_repeated_release_do_not_leak_process_handles"
)
foreach ($testName in $expectedTests) {
    if ($stdout -notmatch [regex]::Escape($testName)) {
        throw "Acceptance output did not contain execution evidence for $testName."
    }
}
if ($stdout -notmatch 'test result: ok\. 12 passed; 0 failed; 7 ignored;') {
    throw "Acceptance output did not contain the expected aggregate 12/12 result."
}

$summary = [pscustomobject]@{
    status = "passed"
    testExecutable = $TestExecutable
    testExecutableSha256 = $testExecutableSha256
    sourceManifest = $sourceManifest
    deadlineSeconds = 45
    requiredScenarios = $expectedTests.Count
    trackedProcessIdentities = $tracked.Count
    processIdentitiesRemaining = 0
    dataScope = "WP-1B-01 acceptance does not access the repository data directory"
    parentAlreadyInJobValidated = $true
    suspendedSpawnValidated = $true
    assignmentFailureRollbackValidated = $true
    resumeFailureRollbackValidated = $true
    multiLevelDescendantsValidated = $true
    dropInsuranceValidated = $true
    handleLeakCheckValidated = $true
}
$summary | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $Evidence "summary.json") -Encoding utf8
$summary
