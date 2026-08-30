[CmdletBinding()]
param(
    [string]$RepoRoot,
    [string]$PythonPath,
    [ValidateRange(1, 100)]
    [int]$QtSamples = 10,
    [switch]$SkipPytest,
    [switch]$SkipQtSmoke,
    [switch]$KeepWorkspace
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not $RepoRoot) {
    $RepoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..\..")).Path
}
else {
    $RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
}

if (-not $PythonPath) {
    $PythonPath = Join-Path $RepoRoot "runtime\python.exe"
}
$PythonPath = (Resolve-Path -LiteralPath $PythonPath).Path

if (-not (Test-Path -LiteralPath (Join-Path $RepoRoot ".git"))) {
    throw "RepoRoot 不是 Sakura Git 工作区：$RepoRoot"
}

$isolationRoot = Join-Path $RepoRoot "temp\runtime-v2-wp-0-01"
$runId = "{0}-{1}" -f (Get-Date -Format "yyyyMMdd-HHmmss"), ([guid]::NewGuid().ToString("N"))
$runRoot = Join-Path $isolationRoot $runId
$workspace = Join-Path $runRoot "workspace"
$resultDir = Join-Path $runRoot "results"
$archivePath = Join-Path $runRoot "source.zip"
$smokeScript = Join-Path $RepoRoot "docs\runtime-v2\baselines\wp_0_01_qt_smoke.py"

function Get-DataManifest {
    param([Parameter(Mandatory = $true)][string]$DataRoot)

    $resolvedDataRoot = (Resolve-Path -LiteralPath $DataRoot).Path
    $items = foreach ($file in Get-ChildItem -LiteralPath $resolvedDataRoot -File -Recurse -Force) {
        $relativePath = [System.IO.Path]::GetRelativePath($resolvedDataRoot, $file.FullName)
        $hash = Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256
        [pscustomobject]@{
            path = $relativePath.Replace("\", "/")
            length = $file.Length
            last_write_utc_ticks = $file.LastWriteTimeUtc.Ticks
            sha256 = $hash.Hash.ToLowerInvariant()
        }
    }
    return @($items | Sort-Object path)
}

function Copy-IsolatedDataSnapshot {
    param(
        [Parameter(Mandatory = $true)][string]$SourceData,
        [Parameter(Mandatory = $true)][string]$DestinationData
    )

    New-Item -ItemType Directory -Path $DestinationData -Force | Out-Null
    foreach ($file in Get-ChildItem -LiteralPath $SourceData -File -Force) {
        Copy-Item -LiteralPath $file.FullName -Destination $DestinationData -Force
    }
    $includedDirectories = @(
        "config",
        "character_studio",
        "chat_history",
        "memory",
        "migration_backup",
        "notes",
        "plugins",
        "runtime_events",
        "visual_observations"
    )
    foreach ($name in $includedDirectories) {
        $source = Join-Path $SourceData $name
        if (Test-Path -LiteralPath $source) {
            Copy-Item -LiteralPath $source -Destination $DestinationData -Recurse -Force
        }
    }
}

function Copy-CharacterPackages {
    param(
        [Parameter(Mandatory = $true)][string]$SourceRoot,
        [Parameter(Mandatory = $true)][string]$DestinationRoot
    )

    if (-not (Test-Path -LiteralPath $SourceRoot)) {
        throw "角色包目录不存在：$SourceRoot"
    }
    if (Test-Path -LiteralPath $DestinationRoot) {
        Remove-VerifiedWorkspace -Target $DestinationRoot
    }
    Copy-Item -LiteralPath $SourceRoot -Destination $DestinationRoot -Recurse -Force
}

function Copy-WorktreeChanges {
    param(
        [Parameter(Mandatory = $true)][string]$SourceRoot,
        [Parameter(Mandatory = $true)][string]$DestinationRoot
    )

    $changedPaths = @(& git -C $SourceRoot diff --name-only HEAD --)
    if ($LASTEXITCODE -ne 0) {
        throw "无法枚举工作区改动，退出码：$LASTEXITCODE"
    }
    foreach ($relativePath in $changedPaths) {
        if (-not $relativePath) {
            continue
        }
        $source = Join-Path $SourceRoot $relativePath
        if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
            continue
        }
        $destination = Join-Path $DestinationRoot $relativePath
        New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
        Copy-Item -LiteralPath $source -Destination $destination -Force
    }
}

function Remove-VerifiedWorkspace {
    param([Parameter(Mandatory = $true)][string]$Target)

    if (-not (Test-Path -LiteralPath $Target)) {
        return
    }
    $resolvedIsolationRoot = (Resolve-Path -LiteralPath $isolationRoot).Path.TrimEnd("\")
    $resolvedTarget = (Resolve-Path -LiteralPath $Target).Path
    $requiredPrefix = "$resolvedIsolationRoot\"
    if (-not $resolvedTarget.StartsWith($requiredPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "拒绝删除隔离根之外的目录：$resolvedTarget"
    }
    Remove-Item -LiteralPath $resolvedTarget -Recurse -Force
}

$startedAt = Get-Date
$dataRoot = Join-Path $RepoRoot "data"
$beforeManifest = $null
$afterManifest = $null
$pytestExitCode = if ($SkipPytest) { $null } else { -1 }
$qtSmokeExitCode = if ($SkipQtSmoke) { $null } else { -1 }
$dataUnchanged = $false
$runError = $null
$cleanupError = $null

New-Item -ItemType Directory -Path $workspace -Force | Out-Null
New-Item -ItemType Directory -Path $resultDir -Force | Out-Null

try {
    $beforeManifest = Get-DataManifest -DataRoot $dataRoot
    $beforeManifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $resultDir "data-before.json") -Encoding UTF8

    & git -C $RepoRoot archive --format=zip --output=$archivePath HEAD
    if ($LASTEXITCODE -ne 0) {
        throw "git archive 失败，退出码：$LASTEXITCODE"
    }
    Expand-Archive -LiteralPath $archivePath -DestinationPath $workspace -Force
    Copy-WorktreeChanges -SourceRoot $RepoRoot -DestinationRoot $workspace
    Copy-IsolatedDataSnapshot -SourceData $dataRoot -DestinationData (Join-Path $workspace "data")
    Copy-CharacterPackages -SourceRoot (Join-Path $RepoRoot "characters") -DestinationRoot (Join-Path $workspace "characters")

    $commit = (& git -C $RepoRoot rev-parse HEAD).Trim()
    Set-Content -LiteralPath (Join-Path $resultDir "commit.txt") -Value $commit -Encoding UTF8

    $previousPythonIoEncoding = $env:PYTHONIOENCODING
    $previousWpWorkspace = $env:SAKURA_WP_0_01_WORKSPACE
    $env:PYTHONIOENCODING = "utf-8"
    $env:SAKURA_WP_0_01_WORKSPACE = $workspace
    try {
        if (-not $SkipPytest) {
            Push-Location $workspace
            try {
                $pytestLauncher = @"
import os
import runpy
import sys
sys.path.insert(0, os.environ["SAKURA_WP_0_01_WORKSPACE"])
sys.argv = ["pytest"]
runpy.run_module("pytest", run_name="__main__")
"@
                $pytestOutput = & $PythonPath -c $pytestLauncher 2>&1
                $pytestExitCode = $LASTEXITCODE
            }
            finally {
                Pop-Location
            }
            $pytestOutput | Set-Content -LiteralPath (Join-Path $resultDir "pytest.log") -Encoding UTF8
            $pytestOutput | ForEach-Object { Write-Host $_ }
        }

        if (-not $SkipQtSmoke) {
            $qtOutput = & $PythonPath $smokeScript --workspace $workspace --samples $QtSamples --result-dir $resultDir 2>&1
            $qtSmokeExitCode = $LASTEXITCODE
            $qtOutput | Set-Content -LiteralPath (Join-Path $resultDir "qt-smoke.log") -Encoding UTF8
            $qtOutput | ForEach-Object { Write-Host $_ }
        }
    }
    finally {
        $env:PYTHONIOENCODING = $previousPythonIoEncoding
        $env:SAKURA_WP_0_01_WORKSPACE = $previousWpWorkspace
    }
}
catch {
    $runError = $_.Exception.ToString()
    Write-Warning $runError
}
finally {
    try {
        $afterManifest = Get-DataManifest -DataRoot $dataRoot
        $afterManifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $resultDir "data-after.json") -Encoding UTF8
        $beforeJson = $beforeManifest | ConvertTo-Json -Depth 4 -Compress
        $afterJson = $afterManifest | ConvertTo-Json -Depth 4 -Compress
        $dataUnchanged = $null -ne $beforeManifest -and $beforeJson -ceq $afterJson
        Set-Content -LiteralPath (Join-Path $resultDir "data-unchanged.txt") -Value $dataUnchanged -Encoding UTF8
    }
    catch {
        $errorParts = @($runError, $_.Exception.ToString()) | Where-Object { $null -ne $_ }
        $runError = $errorParts -join [Environment]::NewLine
    }

    if (-not $KeepWorkspace) {
        try {
            Remove-VerifiedWorkspace -Target $workspace
            if (Test-Path -LiteralPath $archivePath) {
                Remove-Item -LiteralPath $archivePath -Force
            }
        }
        catch {
            $cleanupError = $_.Exception.ToString()
        }
    }
}

$summary = [ordered]@{
    started_at = $startedAt.ToUniversalTime().ToString("o")
    finished_at = (Get-Date).ToUniversalTime().ToString("o")
    repo_root = $RepoRoot
    python = $PythonPath
    result_dir = $resultDir
    pytest_exit_code = $pytestExitCode
    qt_smoke_exit_code = $qtSmokeExitCode
    data_unchanged = $dataUnchanged
    workspace_kept = [bool]$KeepWorkspace
    run_error = $runError
    cleanup_error = $cleanupError
}
$summary | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $resultDir "summary.json") -Encoding UTF8
$summary | ConvertTo-Json -Depth 4 | Write-Host

$failed = (
    $null -ne $runError -or
    $null -ne $cleanupError -or
    -not $dataUnchanged -or
    ($null -ne $pytestExitCode -and $pytestExitCode -ne 0) -or
    ($null -ne $qtSmokeExitCode -and $qtSmokeExitCode -ne 0)
)
if ($failed) {
    exit 1
}
exit 0
