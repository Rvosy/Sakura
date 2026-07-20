$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$scriptPath = Join-Path $PSScriptRoot "windows_managed_process_tree_acceptance.ps1"
$source = Get-Content -LiteralPath $scriptPath -Raw

if ($source -notmatch [regex]::Escape('"--test-threads=1"')) {
    throw "Acceptance must isolate the handle-count scenario with one Rust test thread."
}
if ($source -notmatch [regex]::Escape('src\managed_process_tree.rs') -or
    $source -notmatch [regex]::Escape('$testExecutableInfo.LastWriteTimeUtc -lt $sourceNewest.LastWriteTimeUtc')) {
    throw "Acceptance must reject a test executable older than the WP-1B-01 source."
}

$finallyIndex = $source.LastIndexOf("finally {")
if ($finallyIndex -lt 0) { throw "Acceptance must have a finally cleanup block." }
$summaryIndex = $source.IndexOf('$stdout =', $finallyIndex)
if ($summaryIndex -lt 0) { throw "Acceptance output validation marker was not found." }
$finallyRegion = $source.Substring($finallyIndex, $summaryIndex - $finallyIndex)

$discoverIndex = $finallyRegion.IndexOf('Get-MatchingAcceptanceProcesses')
$stopIndex = $finallyRegion.IndexOf('Stop-ObservedTree')
$remainingIndex = $finallyRegion.LastIndexOf('$remaining')
if ($discoverIndex -lt 0 -or $stopIndex -lt 0 -or $remainingIndex -lt 0) {
    throw "Finally must discover, stop, and rescan every exact-path acceptance process."
}
if ($discoverIndex -gt $stopIndex -or $stopIndex -gt $remainingIndex) {
    throw "Finally cleanup ordering must be discover -> stop -> residual rescan."
}

"WP-1B-01 acceptance cleanup contract passed."
