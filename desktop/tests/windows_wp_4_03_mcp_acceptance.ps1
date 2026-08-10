param()

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if (-not $IsWindows) {
    throw "WP-4-03 MCP acceptance requires Windows."
}

$candidate = Join-Path $repo "desktop\src-tauri\target\debug\sakura-runtime-v2-shell.exe"
$source = Join-Path $repo "tests\fixtures\runtime_v2\wp_0_02\dataset"
$fixtureServer = Join-Path $repo "tests\fixtures\runtime_v2\wp_4_03\stdio_server.py"
$providerServer = Join-Path $repo "tests\fixtures\runtime_v2\wp_4_03\provider_server.py"
$windowsMcp = Join-Path $repo "tools\mcp\Windows-MCP-0.8.0"
$python = Join-Path $repo "runtime\python.exe"
$uv = Join-Path $repo "runtime\Scripts\uv.exe"
$acceptance = Join-Path ([IO.Path]::GetTempPath()) ("sakura-wp-4-03-manual-" + [guid]::NewGuid().ToString("N"))
$appRoot = Join-Path $acceptance "app-root"
$logPath = Join-Path $appRoot "data\logs\sakura-runtime.log"
$mcpPidFile = Join-Path $acceptance "mcp.pid"
$providerPortFile = Join-Path $acceptance "provider.port"
$nonce = [guid]::NewGuid().ToString("N")
$configSentinel = "WP403_CONFIG_PRIVATE_$nonce"
$toolSentinel = "WP403_TOOL_PRIVATE_$nonce"
$providerProcess = $null
$process = $null

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
                try { return $reader.ReadToEnd() } finally { $reader.Dispose() }
            }
            finally {
                $stream.Dispose()
            }
        }
        catch [IO.IOException] {
            if ([DateTime]::UtcNow -ge $deadline) { throw }
            Start-Sleep -Milliseconds 100
        }
    }
}

function Read-RuntimeLogs {
    $directory = Split-Path $logPath
    if (-not (Test-Path -LiteralPath $directory -PathType Container)) { return "" }
    (@(Get-ChildItem -LiteralPath $directory -Filter "sakura-runtime.log*" -File) | ForEach-Object {
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

function Wait-ForRuntimeEvent([string]$EventName, [int]$TimeoutSeconds = 45) {
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        if (@((Read-RuntimeRecords) | Where-Object { $_.event -eq $EventName }).Count -ne 0) {
            return
        }
        Start-Sleep -Milliseconds 200
    }
    throw "Timed out waiting for Runtime event: $EventName"
}

function Wait-ForCoreReady([int]$TimeoutSeconds = 45) {
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        $matches = @((Read-RuntimeRecords) | Where-Object { $_.event -eq "core.readiness.reached" })
        if ($matches.Count -ne 0) {
            $state = [string]$matches[-1].attributes.host_state
            if ($state -in @("ready", "degraded")) { return }
            if ($state -in @("setup_required", "failed")) {
                throw "Core reached an unusable acceptance state: $state"
            }
        }
        Start-Sleep -Milliseconds 200
    }
    throw "Timed out waiting for usable Core readiness."
}

function Get-RelatedProcesses([string]$Root, [string]$Exe) {
    $escapedRoot = [regex]::Escape($Root)
    $escapedExe = [regex]::Escape($Exe)
    @(Get-CimInstance Win32_Process | Where-Object {
        ($_.CommandLine -and $_.CommandLine -match $escapedRoot) -or
        ($_.ExecutablePath -and $_.ExecutablePath -match $escapedExe)
    } | Select-Object ProcessId, ParentProcessId, Name, ExecutablePath, CommandLine)
}

function Assert-IsolatedAcceptanceRoot([string]$Root) {
    $resolved = (Resolve-Path -LiteralPath $Root).Path
    $temp = (Resolve-Path -LiteralPath ([IO.Path]::GetTempPath())).Path.TrimEnd("\")
    if (-not $resolved.StartsWith($temp + "\", [StringComparison]::OrdinalIgnoreCase)) {
        throw "Acceptance cleanup target escaped the system temp directory: $resolved"
    }
    if (-not (Split-Path -Leaf $resolved).StartsWith("sakura-wp-4-03-manual-")) {
        throw "Acceptance cleanup target has an unexpected name: $resolved"
    }
}

Push-Location $repo
try {
    cargo build --locked --manifest-path desktop/src-tauri/Cargo.toml
    if ($LASTEXITCODE -ne 0) { throw "Failed to build the WP-4-03 debug candidate." }
    foreach ($required in @($python, $uv, $fixtureServer, $providerServer, $windowsMcp)) {
        if (-not (Test-Path -LiteralPath $required)) { throw "Missing acceptance dependency: $required" }
    }

    $realDataBefore = Get-FileManifest (Join-Path $repo "data")
    New-Item -ItemType Directory -Path $appRoot -Force | Out-Null
    Copy-Item -Path (Join-Path $source "*") -Destination $appRoot -Recurse -Force
    $fixturePortrait = Join-Path $appRoot "characters\fixture\portraits\placeholder.png"
    Copy-Item -LiteralPath (Join-Path $repo "desktop\src-tauri\icons\icon.png") -Destination $fixturePortrait
    $fixtureManifest = Join-Path $appRoot "characters\fixture\character.json"
    $manifestText = [IO.File]::ReadAllText($fixtureManifest).Replace("portraits/placeholder.txt", "portraits/placeholder.png")
    [IO.File]::WriteAllText($fixtureManifest, $manifestText, [Text.UTF8Encoding]::new($false))

    $providerProcess = Start-Process -FilePath $python -ArgumentList @(
        $providerServer,
        "--port-file", $providerPortFile,
        "--tool-sentinel", $toolSentinel
    ) -WindowStyle Hidden -PassThru
    $providerDeadline = [DateTime]::UtcNow.AddSeconds(10)
    while (-not (Test-Path -LiteralPath $providerPortFile) -and [DateTime]::UtcNow -lt $providerDeadline) {
        if ($providerProcess.HasExited) { throw "Local acceptance provider exited before publishing its port." }
        Start-Sleep -Milliseconds 100
    }
    if (-not (Test-Path -LiteralPath $providerPortFile)) { throw "Local acceptance provider did not start." }
    $providerPort = [int][IO.File]::ReadAllText($providerPortFile)

    $apiConfig = @"
api_profiles:
  - id: fixture
    alias: WP-4-03 Local Provider
    base_url: http://127.0.0.1:$providerPort/v1
    api_key: LOCAL_WP403_KEY
    models:
      - name: fixture-model
model_slots:
  chat:
    profile_id: fixture
    model: fixture-model
config_version: 4
"@
    [IO.File]::WriteAllText((Join-Path $appRoot "data\config\api.yaml"), $apiConfig, [Text.UTF8Encoding]::new($false))
    Add-Content -LiteralPath (Join-Path $appRoot "data\config\system_config.yaml") -Value "`nmcp:`n  windows_enabled: true" -Encoding utf8

    $mcpConfig = [ordered]@{
        enabled = $true
        default_call_timeout = 2
        servers = [ordered]@{
            windows = [ordered]@{
                enabled = $true
                transport = "stdio"
                command = $uv
                args = @("--directory", $windowsMcp, "run", "windows-mcp", "serve", "--tools", "App,Snapshot,Screenshot,Click,Type,Wait")
                env = [ordered]@{ ANONYMIZED_TELEMETRY = "false"; WP403_PRIVATE = $configSentinel }
                name_prefix = "windows__"
                call_timeout = 30
                risk = "high"
                requires_confirmation = $true
            }
            acceptance_fixture = [ordered]@{
                enabled = $true
                transport = "stdio"
                command = $python
                args = @($fixtureServer, "--pid-file", $mcpPidFile)
                env = [ordered]@{ WP403_PRIVATE = $configSentinel }
                name_prefix = "fixture__"
                call_timeout = 2
                risk = "high"
                requires_confirmation = $true
            }
        }
    }
    $mcpJson = $mcpConfig | ConvertTo-Json -Depth 8
    [IO.File]::WriteAllText((Join-Path $appRoot "data\config\mcp.yaml"), $mcpJson, [Text.UTF8Encoding]::new($false))

    Write-Host ""
    Write-Host "WP-4-03 MCP 实机验收" -ForegroundColor Cyan
    Write-Host "隔离根：$appRoot"
    Write-Host "Core readiness 不等待 MCP；Windows MCP 首次启动仍可能需要一些时间。"
    Write-Host ""

    $oldManualRoot = $env:SAKURA_WP_4_01_MANUAL_ROOT
    $oldLogLevel = $env:SAKURA_RUNTIME_V2_LOG_LEVEL
    try {
        $env:SAKURA_WP_4_01_MANUAL_ROOT = $appRoot
        $env:SAKURA_RUNTIME_V2_LOG_LEVEL = "debug"
        $process = Start-Process -FilePath $candidate -PassThru
        Wait-ForRuntimeEvent "shell.ready"
        Wait-ForCoreReady
        Wait-ForRuntimeEvent "mcp.server.ready"

        Write-Host "请在 Sakura 中依次完成：" -ForegroundColor Yellow
        Write-Host "1. 打开设置的 Tools 区域，确认 Windows MCP 与 acceptance_fixture 均显示 ready。"
        Write-Host "2. 发送“测试 MCP 成功”，在原生确认框中允许；确认聊天最终显示调用完成。"
        Write-Host "3. 发送“测试 MCP 拒绝”，在原生确认框中拒绝；确认没有执行并且聊天可继续。"
        Write-Host "4. 发送“测试 MCP 超时”，在原生确认框中允许；确认超时有稳定错误且应用仍可用。"
        Write-Host "5. 用任务管理器结束命令行含 app.core_host 且含隔离根的 python.exe，等待自动恢复。"
        Write-Host "6. 恢复后重新打开设置，确认两个 server 状态恢复；然后从菜单正常退出 Sakura。"
        Write-Host ""
        $process.WaitForExit()
        if ($process.ExitCode -ne 0) { throw "WP-4-03 candidate exited with code $($process.ExitCode)." }
    }
    finally {
        if ($process -and -not $process.HasExited) {
            Stop-Process -Id $process.Id -Force
            $process.WaitForExit()
        }
        $env:SAKURA_WP_4_01_MANUAL_ROOT = $oldManualRoot
        $env:SAKURA_RUNTIME_V2_LOG_LEVEL = $oldLogLevel
    }

    if ($providerProcess -and -not $providerProcess.HasExited) {
        Stop-Process -Id $providerProcess.Id -Force
        $providerProcess.WaitForExit()
    }
    Start-Sleep -Milliseconds 750
    $related = Get-RelatedProcesses $acceptance $candidate
    if ($related.Count -ne 0) {
        throw "WP-4-03 left related Shell/Core/MCP processes: $($related | ConvertTo-Json -Compress)"
    }

    $logs = Read-RuntimeLogs
    foreach ($forbidden in @($configSentinel, $toolSentinel, $appRoot, $python, $uv, $windowsMcp, $fixtureServer)) {
        if ($logs.Contains($forbidden)) { throw "Private MCP value reached persistent logs: $forbidden" }
    }
    $records = Read-RuntimeRecords
    $events = @($records | ForEach-Object { $_.event })
    foreach ($requiredEvent in @("mcp.server.ready", "webview.settings.opened", "webview.chat.send", "shell.stopped")) {
        if ($requiredEvent -notin $events) { throw "Required MCP acceptance event was not found: $requiredEvent" }
    }
    $generations = @($records | Where-Object { $_.generation_number } | ForEach-Object { [int64]$_.generation_number } | Sort-Object -Unique)
    if ($generations.Count -lt 2) { throw "Core recovery did not produce at least two generations." }

    $realDataAfter = Get-FileManifest (Join-Path $repo "data")
    $realDataChanged = Compare-FileManifest $realDataBefore $realDataAfter
    if ($realDataChanged.Count -ne 0) { throw "WP-4-03 changed repository data: $($realDataChanged -join ', ')" }

    Assert-IsolatedAcceptanceRoot $acceptance
    Remove-Item -LiteralPath $acceptance -Recurse -Force
    if (Test-Path -LiteralPath $acceptance) { throw "WP-4-03 isolated acceptance root was not removed." }
    [ordered]@{
        status = "manual_session_completed"
        candidate = $candidate
        generations_observed = $generations
        sensitive_log_hits = 0
        related_process_residue = 0
        real_data_changed = @()
        acceptance_root_removed = $true
        next = "Return the checklist result to the project owner; this script does not self-accept WP-4-03."
    } | ConvertTo-Json -Compress
}
finally {
    if ($providerProcess -and -not $providerProcess.HasExited) {
        Stop-Process -Id $providerProcess.Id -Force
        $providerProcess.WaitForExit()
    }
    Pop-Location
}
