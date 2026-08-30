param()

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if (-not $IsWindows) {
    throw "WP-4L-01 observability acceptance requires Windows."
}

$candidate = Join-Path $repo "desktop\src-tauri\target\debug\sakura.exe"
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

function Read-SharedText([string]$Path, [int]$TimeoutSeconds = 5) {
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ($true) {
        try {
            $share = [IO.FileShare]([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete)
            $stream = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, $share)
            try {
                $reader = [IO.StreamReader]::new($stream, [Text.Encoding]::UTF8, $true)
                try {
                    return $reader.ReadToEnd()
                }
                finally {
                    $reader.Dispose()
                }
            }
            finally {
                $stream.Dispose()
            }
        }
        catch [IO.IOException] {
            if ([DateTime]::UtcNow -ge $deadline) {
                throw
            }
            Start-Sleep -Milliseconds 100
        }
    }
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
            $contents = Read-SharedText $logPath
            if ($contents.Contains(('"event":"' + $EventName + '"'))) {
                return
            }
        }
        Start-Sleep -Milliseconds 200
    }
    throw "Timed out waiting for Runtime log event: $EventName"
}

function Read-RuntimeLogs {
    $logDirectory = Split-Path $logPath
    if (-not (Test-Path -LiteralPath $logDirectory -PathType Container)) {
        return ""
    }
    (@(Get-ChildItem -LiteralPath $logDirectory -Filter "sakura-runtime.log*" -File) | ForEach-Object {
        Read-SharedText $_.FullName
    }) -join "`n"
}

function Read-RuntimeRecords {
    @((Read-RuntimeLogs) -split "`r?`n" | ForEach-Object {
        if ($_.Trim()) {
            try { $_ | ConvertFrom-Json } catch { }
        }
    })
}

function Wait-ForCoreReady([int]$TimeoutSeconds = 30) {
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        $matches = @((Read-RuntimeRecords) | Where-Object { $_.event -eq "core.readiness.reached" })
        if ($matches.Count -ne 0) {
            $state = [string]$matches[-1].attributes.host_state
            if ($state -in @("ready", "degraded")) {
                return
            }
            if ($state -in @("setup_required", "failed")) {
                throw "Core reached an unusable acceptance state: $state"
            }
        }
        Start-Sleep -Milliseconds 200
    }
    throw "Timed out waiting for a usable Core readiness state."
}

function Wait-ForCharacterPresentation([int]$TimeoutSeconds = 30) {
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        $completed = @((Read-RuntimeRecords) | Where-Object {
            $_.event -eq "webview.command.completed" -and
            $_.attributes.command -eq "current_character_presentation"
        })
        if ($completed.Count -ne 0) {
            return
        }
        Start-Sleep -Milliseconds 200
    }
    throw "Timed out waiting for the isolated character presentation."
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
    $fixtureManifest = Join-Path $appRoot "characters\fixture\character.json"
    $fixturePortrait = Join-Path $appRoot "characters\fixture\portraits\placeholder.png"
    Copy-Item -LiteralPath (Join-Path $repo "desktop\src-tauri\icons\icon.png") -Destination $fixturePortrait
    $fixtureManifestText = [IO.File]::ReadAllText($fixtureManifest)
    $fixtureManifestText = $fixtureManifestText.Replace(
        "portraits/placeholder.txt",
        "portraits/placeholder.png"
    )
    [IO.File]::WriteAllText($fixtureManifest, $fixtureManifestText, [Text.UTF8Encoding]::new($false))
    $apiConfig = Join-Path $appRoot "data\config\api.yaml"
    $apiText = [IO.File]::ReadAllText($apiConfig)
    $apiText = [regex]::Replace(
        $apiText,
        "(?m)^(\s*)api_key:\s*.*$",
        { param($match) $match.Groups[1].Value + "api_key: $secretSentinel" }
    )
    [IO.File]::WriteAllText($apiConfig, $apiText, [Text.UTF8Encoding]::new($false))
    $legacyMemoryLog = Join-Path $appRoot "data\logs\memory-initialization.jsonl"
    New-Item -ItemType Directory -Path (Split-Path $legacyMemoryLog) -Force | Out-Null
    $legacyMemoryContents = "LEGACY_MEMORY_DIAGNOSTIC_$nonce`n"
    [IO.File]::WriteAllText($legacyMemoryLog, $legacyMemoryContents, [Text.UTF8Encoding]::new($false))
    $isolatedBefore = Get-FileManifest $appRoot

    Write-Host ""
    Write-Host "WP-4L-01 Runtime v2 可观测性实机验收" -ForegroundColor Cyan
    Write-Host "隔离根：$appRoot"
    Write-Host "脚本会先验证第二实例不进入日志 writer；看到“已在运行”提示后请关闭提示框。"
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
        Wait-ForCoreReady
        Wait-ForCharacterPresentation
        $second = Start-Process -FilePath $candidate -PassThru
        $secondPid = $second.Id
        if (-not $second.WaitForExit(15000)) {
            Stop-Process -Id $second.Id -Force
            throw "Second instance did not leave the shared-lock conflict path in time."
        }
        if ($second.ExitCode -ne 0) {
            throw "Second instance exited with code $($second.ExitCode)."
        }
        Start-Sleep -Milliseconds 500
        $secondPidToken = '"pid":' + $secondPid + ','
        if ((Read-RuntimeLogs).Contains($secondPidToken)) {
            throw "The second-instance conflict path entered the Runtime log writer (PID $secondPid)."
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
        if ($process -and -not $process.HasExited) {
            Stop-Process -Id $process.Id -Force
            $process.WaitForExit()
        }
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
            if ($record.schema_version -eq 1) { $record }
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
