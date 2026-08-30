param()

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if (-not $IsWindows) {
    throw "WP-4-01 visible Memory acceptance requires Windows."
}

$candidate = Join-Path $repo "desktop\src-tauri\target\debug\sakura.exe"
$source = Join-Path $repo "tests\fixtures\runtime_v2\wp_0_02\dataset"
$acceptance = Join-Path ([IO.Path]::GetTempPath()) ("sakura-wp-4-01-manual-" + [guid]::NewGuid().ToString("N"))
$appRoot = Join-Path $acceptance "app-root"
$legacyMemory = Join-Path $appRoot "data\memory.json"
$python = Join-Path $repo "runtime\python.exe"

function Get-FileManifest([string]$Root) {
    $manifest = @{}
    Get-ChildItem -LiteralPath $Root -Recurse -File | ForEach-Object {
        $relative = $_.FullName.Substring($Root.Length + 1).Replace("\", "/")
        $manifest[$relative] = [ordered]@{
            size = $_.Length
            sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName).Hash
        }
    }
    $manifest
}

function Get-ChangedPaths([hashtable]$Before, [hashtable]$After) {
    @($Before.Keys + $After.Keys | Sort-Object -Unique | Where-Object {
        -not $Before.ContainsKey($_) -or
        -not $After.ContainsKey($_) -or
        $Before[$_].size -ne $After[$_].size -or
        $Before[$_].sha256 -ne $After[$_].sha256
    })
}

function Get-RelatedProcesses([string]$Root, [string]$Exe) {
    $escapedRoot = [regex]::Escape($Root)
    $escapedExe = [regex]::Escape($Exe)
    @(Get-CimInstance Win32_Process | Where-Object {
        ($_.CommandLine -and $_.CommandLine -match $escapedRoot) -or
        ($_.ExecutablePath -and $_.ExecutablePath -match $escapedExe)
    } | Select-Object ProcessId, ParentProcessId, Name, ExecutablePath, CommandLine)
}

Push-Location $repo
try {
    cargo build --locked --manifest-path desktop/src-tauri/Cargo.toml
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to build the WP-4-01 debug Tauri candidate."
    }
    New-Item -ItemType Directory -Path $appRoot -Force | Out-Null
    Copy-Item -Path (Join-Path $source "*") -Destination $appRoot -Recurse -Force
    New-Item -ItemType File -Path (Join-Path $acceptance ".sakura-wp-4-01-manual") -Force | Out-Null
    if (-not (Test-Path -LiteralPath $legacyMemory)) {
        [IO.File]::WriteAllBytes($legacyMemory, [Text.Encoding]::UTF8.GetBytes("legacy-memory-byte-baseline`n"))
    }
    $manifestBefore = Get-FileManifest $appRoot
    $legacyHashBefore = (Get-FileHash -Algorithm SHA256 -LiteralPath $legacyMemory).Hash

    Write-Host ""
    Write-Host "WP-4-01 可见 UI 验收已启动。请按 Codex 给出的清单操作。" -ForegroundColor Cyan
    Write-Host "这是隔离数据根，不会写入仓库 data/；请从托盘/右键菜单打开设置。"
    Write-Host "完成后从 Sakura 菜单正常退出应用，脚本会继续检查残留。"
    Write-Host ""

    $oldManualRoot = $env:SAKURA_WP_4_01_MANUAL_ROOT
    $oldHfHome = $env:HF_HOME
    $oldSentenceHome = $env:SENTENCE_TRANSFORMERS_HOME
    try {
        $env:SAKURA_WP_4_01_MANUAL_ROOT = $appRoot
        $env:HF_HOME = (Join-Path $appRoot "runtime\hf-cache")
        $env:SENTENCE_TRANSFORMERS_HOME = (Join-Path $appRoot "runtime\hf-cache\hub")
        $process = Start-Process -FilePath $candidate -PassThru
        $process.WaitForExit()
        if ($process.ExitCode -ne 0) {
            throw "WP-4-01 candidate exited with code $($process.ExitCode)."
        }
    }
    finally {
        $env:SAKURA_WP_4_01_MANUAL_ROOT = $oldManualRoot
        $env:HF_HOME = $oldHfHome
        $env:SENTENCE_TRANSFORMERS_HOME = $oldSentenceHome
    }

    Start-Sleep -Milliseconds 500
    $related = Get-RelatedProcesses $acceptance $candidate
    if ($related.Count -ne 0) {
        throw "WP-4-01 left related Shell/Core descendants: $($related | ConvertTo-Json -Compress)"
    }
    $legacyHashAfter = (Get-FileHash -Algorithm SHA256 -LiteralPath $legacyMemory).Hash
    if ($legacyHashAfter -ne $legacyHashBefore) {
        throw "Legacy data/memory.json changed during WP-4-01 acceptance."
    }

    & $python -c "from app.core.instance import InstanceAcquireStatus, SingleInstanceGuard; g=SingleInstanceGuard(); s=g.acquire(); assert s is InstanceAcquireStatus.ACQUIRED, s; g.release()"
    if ($LASTEXITCODE -ne 0) {
        throw "WP-4-01 shared application lock could not be reacquired immediately."
    }

    $manifestAfter = Get-FileManifest $appRoot
    $changed = Get-ChangedPaths $manifestBefore $manifestAfter
    $unexpected = @($changed | Where-Object {
        $_ -ne "data/memory_curation_state.json" -and
        $_ -notlike "data/memory/*" -and
        $_ -notlike "data/chat_history/*" -and
        $_ -ne "data/config/system_config.yaml" -and
        $_ -ne "data/config/api.yaml" -and
        $_ -notlike "runtime/hf-cache/*"
    })
    if ($unexpected.Count -ne 0) {
        throw "WP-4-01 changed paths outside the approved Memory/config/history set: $($unexpected -join ', ')"
    }

    Remove-Item -LiteralPath $acceptance -Recurse -Force
    if (Test-Path -LiteralPath $acceptance) {
        throw "WP-4-01 isolated acceptance root was not removed."
    }
    [ordered]@{
        status = "manual_session_completed"
        candidate = $candidate
        acceptance_root_removed = $true
        legacy_memory_unchanged = $true
        shared_lock_reacquired = $true
        related_process_residue = 0
        changed_paths = $changed
        next = "Return to Codex and report the checklist result; this script does not self-accept WP-4-01."
    } | ConvertTo-Json -Compress
}
finally {
    Pop-Location
}
