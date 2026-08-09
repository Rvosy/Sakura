param()

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if (-not $IsWindows) {
    throw "WP-4L-01 observability acceptance requires Windows."
}

$candidate = Join-Path $repo "desktop\src-tauri\target\debug\sakura-runtime-v2-shell.exe"
$source = Join-Path $repo "tests\fixtures\runtime_v2\wp_0_02\dataset"
$acceptance = Join-Path ([IO.Path]::GetTempPath()) ("sakura-wp-4-01-manual-" + [guid]::NewGuid().ToString("N"))
$appRoot = Join-Path $acceptance "app-root"
$logPath = Join-Path $appRoot "data\logs\sakura-runtime.log"
$python = Join-Path $repo "runtime\python.exe"
$nonce = [guid]::NewGuid().ToString("N")
$secretSentinel = "sk-WP4L01-SECRET-$nonce"
$chatSentinel = "WP4L01_CHAT_BODY_$nonce"
$toolSentinel = "WP4L01_TOOL_ARGUMENT_$nonce"

function Get-FileManifest([string]$Root) {
    $manifest = @{}
    if (-not (Test-Path -LiteralPath $Root -PathType Container)) {
        return $manifest
    }
    Get-ChildItem -LiteralPath $Root -Recurse -File | ForEach-Object {
        $relative = $_.FullName.Substring($Root.Length + 1).Replace("\", "/")
        $manifest[$relative] = [ordered]@{
            size = $_.Length
            sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName).Hash
        }
    }
    $manifest
}

function Compare-FileManifest([hashtable]$Before, [hashtable]$After) {
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

function Wait-ForLogEvent([string]$EventName, [int]$TimeoutSeconds = 30) {
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        if (Test-Path -LiteralPath $logPath -PathType Leaf) {
            $contents = [IO.File]::ReadAllText($logPath)
            if ($contents.Contains(('"event":"' + $EventName + '"'))) {
                return
            }
        }
        Start-Sleep -Milliseconds 200
    }
    throw "Timed out waiting for Runtime log event: $EventName"
}

function Wait-ForStableLog([int]$TimeoutSeconds = 10) {
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    $previous = ""
    while ([DateTime]::UtcNow -lt $deadline) {
        if (Test-Path -LiteralPath $logPath -PathType Leaf) {
            $file = Get-Item -LiteralPath $logPath
            $current = "$($file.Length):$($file.LastWriteTimeUtc.Ticks)"
            if ($current -eq $previous) {
                return $current
            }
            $previous = $current
        }
        Start-Sleep -Milliseconds 500
    }
    throw "Runtime log did not become stable before the second-instance check."
}

function Assert-IsolatedAcceptanceRoot([string]$Root) {
    $resolved = (Resolve-Path -LiteralPath $Root).Path
    $temp = (Resolve-Path -LiteralPath ([IO.Path]::GetTempPath())).Path.TrimEnd("\")
    if (-not $resolved.StartsWith($temp + "\", [StringComparison]::OrdinalIgnoreCase)) {
        throw "Acceptance cleanup target escaped the system temp directory: $resolved"
    }
    if (-not (Split-Path -Leaf $resolved).StartsWith("sakura-wp-4-01-manual-")) {
        throw "Acceptance cleanup target has an unexpected name: $resolved"
    }
}

Push-Location $repo
try {
    cargo build --locked --manifest-path desktop/src-tauri/Cargo.toml
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to build the WP-4L-01 debug Tauri candidate."
    }

    $realDataBefore = Get-FileManifest (Join-Path $repo "data")
    New-Item -ItemType Directory -Path $appRoot -Force | Out-Null
    Copy-Item -Path (Join-Path $source "*") -Destination $appRoot -Recurse -Force
    New-Item -ItemType File -Path (Join-Path $acceptance ".sakura-wp-4-01-manual") -Force | Out-Null
    $apiConfig = Join-Path $appRoot "data\config\api.yaml"
    $apiText = [IO.File]::ReadAllText($apiConfig)
    $apiText = [regex]::Replace($apiText, "(?m)^\s*api_key:\s*.*$", "    api_key: $secretSentinel")
    [IO.File]::WriteAllText($apiConfig, $apiText, [Text.UTF8Encoding]::new($false))
    $legacyMemoryLog = Join-Path $appRoot "data\logs\memory-initialization.jsonl"
    New-Item -ItemType Directory -Path (Split-Path $legacyMemoryLog) -Force | Out-Null
    $legacyMemoryContents = "LEGACY_MEMORY_DIAGNOSTIC_$nonce`n"
    [IO.File]::WriteAllText($legacyMemoryLog, $legacyMemoryContents, [Text.UTF8Encoding]::new($false))
    $isolatedBefore = Get-FileManifest $appRoot

    Write-Host ""
    Write-Host "WP-4L-01 Runtime v2 可观测性实机验收" -ForegroundColor Cyan
    Write-Host "隔离根：$appRoot"
    Write-Host "脚本会先验证第二实例不触碰日志；看到“已在运行”提示后请关闭提示框。"
    Write-Host ""

    $oldManualRoot = $env:SAKURA_WP_4_01_MANUAL_ROOT
    $oldLogLevel = $env:SAKURA_RUNTIME_V2_LOG_LEVEL
    $oldHfHome = $env:HF_HOME
    $oldSentenceHome = $env:SENTENCE_TRANSFORMERS_HOME
    try {
        $env:SAKURA_WP_4_01_MANUAL_ROOT = $appRoot
        $env:SAKURA_RUNTIME_V2_LOG_LEVEL = "debug"
        $env:HF_HOME = (Join-Path $appRoot "runtime\hf-cache")
        $env:SENTENCE_TRANSFORMERS_HOME = (Join-Path $appRoot "runtime\hf-cache\hub")

        $process = Start-Process -FilePath $candidate -PassThru
        Wait-ForLogEvent "shell.ready"
        Wait-ForLogEvent "core.readiness.reached"
        $fingerprintBeforeConflict = Wait-ForStableLog
        $second = Start-Process -FilePath $candidate -PassThru
        if (-not $second.WaitForExit(15000)) {
            Stop-Process -Id $second.Id -Force
            throw "Second instance did not leave the shared-lock conflict path in time."
        }
        if ($second.ExitCode -ne 0) {
            throw "Second instance exited with code $($second.ExitCode)."
        }
        $fingerprintAfterConflict = Wait-ForStableLog
        if ($fingerprintAfterConflict -ne $fingerprintBeforeConflict) {
            throw "The second-instance conflict path changed the Runtime log."
        }

        Write-Host ""
        Write-Host "请在第一个 Sakura 实例中依次完成以下操作：" -ForegroundColor Yellow
        Write-Host "1. 在聊天框发送：$chatSentinel"
        Write-Host "2. 打开设置，进入 Tools 页面并执行一次读取/保存；进入 Memory 搜索：$toolSentinel"
        Write-Host "3. 用任务管理器结束命令行含 app.core_host 且含隔离根的 python.exe，等待桌宠自动重连。"
        Write-Host "4. 重连后再打开一次设置，确认 Tools/Memory 可用，然后正常关闭设置并从菜单退出 Sakura。"
        Write-Host ""
        $process.WaitForExit()
        if ($process.ExitCode -ne 0) {
            throw "WP-4L-01 candidate exited with code $($process.ExitCode)."
        }
    }
    finally {
        $env:SAKURA_WP_4_01_MANUAL_ROOT = $oldManualRoot
        $env:SAKURA_RUNTIME_V2_LOG_LEVEL = $oldLogLevel
        $env:HF_HOME = $oldHfHome
        $env:SENTENCE_TRANSFORMERS_HOME = $oldSentenceHome
    }

    Start-Sleep -Milliseconds 750
    $related = Get-RelatedProcesses $acceptance $candidate
    if ($related.Count -ne 0) {
        throw "WP-4L-01 left related Shell/Core descendants: $($related | ConvertTo-Json -Compress)"
    }
    & $python -c "from app.core.instance import InstanceAcquireStatus, SingleInstanceGuard; g=SingleInstanceGuard(); s=g.acquire(); assert s is InstanceAcquireStatus.ACQUIRED, s; g.release()"
    if ($LASTEXITCODE -ne 0) {
        throw "WP-4L-01 shared application lock could not be reacquired immediately."
    }

    $logFiles = @(Get-ChildItem -LiteralPath (Split-Path $logPath) -Filter "sakura-runtime.log*" -File)
    if ($logFiles.Count -eq 0) {
        throw "WP-4L-01 produced no unified Runtime log."
    }
    $allLogs = ($logFiles | ForEach-Object { [IO.File]::ReadAllText($_.FullName) }) -join "`n"
    foreach ($forbidden in @($secretSentinel, $chatSentinel, $toolSentinel, $appRoot, "generationCredential", "generation_credential")) {
        if ($allLogs.Contains($forbidden)) {
            throw "Sensitive acceptance sentinel reached persistent logs: $forbidden"
        }
    }
    if (-not (Test-Path -LiteralPath $legacyMemoryLog -PathType Leaf)) {
        throw "WP-4L-01 deleted the retired Memory diagnostic file."
    }
    if ([IO.File]::ReadAllText($legacyMemoryLog) -ne $legacyMemoryContents) {
        throw "WP-4L-01 resumed writing the retired Memory diagnostic file."
    }

    $records = @($logFiles | Sort-Object Name -Descending | ForEach-Object {
        [IO.File]::ReadLines($_.FullName) | Where-Object { $_.Trim() } | ForEach-Object {
            $record = $_ | ConvertFrom-Json
            if ($record.schema_version -eq 2) { $record }
        }
    })
    $events = @($records | ForEach-Object { $_.event })
    foreach ($required in @(
        "shell.started",
        "shell.ready",
        "shell.stopped",
        "core.spawn.started",
        "core.spawn.completed",
        "core.hello.completed",
        "core.initialize.completed",
        "core.readiness.reached",
        "core.restart.scheduled",
        "ipc.request.completed",
        "webview.chat.send",
        "agent.turn.started",
        "webview.settings.opened",
        "webview.memory.request",
        "webview.tools.request"
    )) {
        if ($required -notin $events) {
            throw "Required observability event was not found: $required"
        }
    }
    if ("webview.settings.closed" -notin $events -and "settings_window_destroyed" -notin $events) {
        throw "No settings close event was found in the unified Runtime log."
    }
    $generationNumbers = @($records | Where-Object { $_.generation_number } | ForEach-Object { [int64]$_.generation_number } | Sort-Object -Unique)
    if ($generationNumbers.Count -lt 2) {
        throw "Core crash/recovery did not produce at least two observed generations."
    }

    $realDataAfter = Get-FileManifest (Join-Path $repo "data")
    $realDataChanged = Compare-FileManifest $realDataBefore $realDataAfter
    if ($realDataChanged.Count -ne 0) {
        throw "WP-4L-01 changed real repository data paths: $($realDataChanged -join ', ')"
    }
    $isolatedAfter = Get-FileManifest $appRoot
    $isolatedChanged = Compare-FileManifest $isolatedBefore $isolatedAfter

    Assert-IsolatedAcceptanceRoot $acceptance
    Remove-Item -LiteralPath $acceptance -Recurse -Force
    if (Test-Path -LiteralPath $acceptance) {
        throw "WP-4L-01 isolated acceptance root was not removed."
    }
    [ordered]@{
        status = "manual_session_completed"
        candidate = $candidate
        required_events = $true
        sensitive_log_hits = 0
        real_data_changed = @()
        isolated_changed_paths = $isolatedChanged
        generations_observed = $generationNumbers
        related_process_residue = 0
        shared_lock_reacquired = $true
        acceptance_root_removed = $true
        next = "Return the checklist result to the project owner; this script does not self-accept WP-4L-01."
    } | ConvertTo-Json -Compress
}
finally {
    Pop-Location
}
