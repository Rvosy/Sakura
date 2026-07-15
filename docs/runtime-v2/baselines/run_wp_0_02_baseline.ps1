[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
$dataRoot = (Resolve-Path (Join-Path $repoRoot "data")).Path
$python = Join-Path $repoRoot "runtime\python.exe"
$contractScript = Join-Path $PSScriptRoot "wp_0_02_contract.py"
$fixtureRoot = Join-Path $repoRoot "tests\fixtures\runtime_v2\wp_0_02"
$testFile = Join-Path $repoRoot "tests\unit\test_wp_0_02_data_contract.py"
$runId = "wp-0-02-{0}-{1}" -f (Get-Date -Format "yyyyMMdd-HHmmss"), ([Guid]::NewGuid().ToString("N").Substring(0, 8))
$resultRoot = Join-Path $repoRoot (Join-Path "temp\runtime-v2-wp-0-02" $runId)
$contractOutput = Join-Path $resultRoot "contract"
$pytestBaseTemp = Join-Path $resultRoot "pytest-basetemp"
[System.IO.Directory]::CreateDirectory($resultRoot) | Out-Null

function Get-DataManifest {
    param([Parameter(Mandatory = $true)][string]$Root)

    $records = [System.Collections.Generic.List[object]]::new()
    Get-ChildItem -LiteralPath $Root -File -Recurse -Force |
        Sort-Object FullName |
        ForEach-Object {
            $relative = [System.IO.Path]::GetRelativePath($Root, $_.FullName).Replace("\", "/")
            $records.Add([ordered]@{
                path = $relative
                length = [int64]$_.Length
                mtime_utc = $_.LastWriteTimeUtc.ToString("o")
                sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            })
        }
    return @($records)
}

function Get-TextSha256 {
    param([Parameter(Mandatory = $true)][string]$Text)

    $bytes = [System.Text.Encoding]::UTF8.GetBytes($Text)
    $hash = [System.Security.Cryptography.SHA256]::HashData($bytes)
    return [Convert]::ToHexString($hash).ToLowerInvariant()
}

function Write-JsonFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)]$Value
    )

    $json = $Value | ConvertTo-Json -Depth 8
    [System.IO.File]::WriteAllText($Path, $json + [Environment]::NewLine, [System.Text.UTF8Encoding]::new($false))
}

Push-Location $repoRoot
try {
    $before = Get-DataManifest -Root $dataRoot
    $beforeCanonical = $before | ConvertTo-Json -Depth 8 -Compress
    Write-JsonFile -Path (Join-Path $resultRoot "data-before.json") -Value $before

    $failure = $null
    try {
        & $python $contractScript --fixture-root $fixtureRoot --output-root $contractOutput
        if ($LASTEXITCODE -ne 0) {
            throw "WP-0-02 contract exited with code $LASTEXITCODE"
        }

        & $python -m pytest $testFile -q "--basetemp=$pytestBaseTemp"
        if ($LASTEXITCODE -ne 0) {
            throw "WP-0-02 pytest exited with code $LASTEXITCODE"
        }
    }
    catch {
        $failure = $_
    }
    finally {
        $after = Get-DataManifest -Root $dataRoot
        $afterCanonical = $after | ConvertTo-Json -Depth 8 -Compress
        Write-JsonFile -Path (Join-Path $resultRoot "data-after.json") -Value $after

        $dataUnchanged = $beforeCanonical -ceq $afterCanonical
        $summary = [ordered]@{
            work_package = "WP-0-02"
            result_root = $resultRoot
            data_files = $before.Count
            data_manifest_sha256_before = Get-TextSha256 -Text $beforeCanonical
            data_manifest_sha256_after = Get-TextSha256 -Text $afterCanonical
            data_unchanged = $dataUnchanged
            contract_report = Join-Path $contractOutput "report.json"
            pytest_basetemp = $pytestBaseTemp
        }
        Write-JsonFile -Path (Join-Path $resultRoot "summary.json") -Value $summary

        if (-not $dataUnchanged) {
            $beforeByPath = @{}
            foreach ($item in $before) { $beforeByPath[$item.path] = $item }
            $afterByPath = @{}
            foreach ($item in $after) { $afterByPath[$item.path] = $item }
            $changed = @(
                ($beforeByPath.Keys + $afterByPath.Keys) |
                    Sort-Object -Unique |
                    Where-Object {
                        -not $beforeByPath.ContainsKey($_) -or
                        -not $afterByPath.ContainsKey($_) -or
                        (($beforeByPath[$_] | ConvertTo-Json -Compress) -cne ($afterByPath[$_] | ConvertTo-Json -Compress))
                    }
            )
            throw "Real data/ changed during WP-0-02 acceptance: $($changed -join ', ')"
        }
    }

    if ($null -ne $failure) {
        throw $failure
    }

    Write-Output "WP-0-02 acceptance passed"
    Write-Output "RESULT_ROOT=$resultRoot"
    Write-Output "DATA_FILES=$($before.Count)"
    Write-Output "DATA_MANIFEST_SHA256=$(Get-TextSha256 -Text $beforeCanonical)"
    Write-Output "DATA_UNCHANGED=True"
}
finally {
    Pop-Location
}
