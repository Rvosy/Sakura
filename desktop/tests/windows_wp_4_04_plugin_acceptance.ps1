param()

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if (-not $IsWindows) {
    throw "WP-4-04 plugin acceptance requires Windows."
}

$candidate = Join-Path $repo "desktop\src-tauri\target\debug\sakura.exe"
$source = Join-Path $repo "tests\fixtures\runtime_v2\wp_0_02\dataset"
$pluginSource = Join-Path $repo "tests\fixtures\runtime_v2\wp_4_04\plugins"
$providerServer = Join-Path $repo "tests\fixtures\runtime_v2\wp_4_04\provider_server.py"
$python = Join-Path $repo "runtime\python.exe"
$acceptance = Join-Path ([IO.Path]::GetTempPath()) ("sakura-wp-4-04-manual-" + [guid]::NewGuid().ToString("N"))
$appRoot = Join-Path $acceptance "app-root"
$logPath = Join-Path $appRoot "data\logs\sakura-runtime.log"
$providerPortFile = Join-Path $acceptance "provider.port"
$nonce = [guid]::NewGuid().ToString("N")
$toolSentinel = "WP404_TOOL_PRIVATE_$nonce"
$settingsSentinel = "WP404_SETTINGS_PRIVATE_$nonce"
$providerProcess = $null
$process = $null

function Get-FileManifest([string]$Root) {
    $manifest = @{}
    if (-not (Test-Path -LiteralPath $Root -PathType Container)) { return $manifest }
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
            finally { $stream.Dispose() }
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
        if ($_.Trim()) { try { $_ | ConvertFrom-Json } catch { } }
    })
}

function Wait-ForRuntimeEvent([string]$EventName, [int]$TimeoutSeconds = 45) {
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        if (@((Read-RuntimeRecords) | Where-Object { $_.event -eq $EventName }).Count -ne 0) { return }
        Start-Sleep -Milliseconds 200
    }
    throw "Timed out waiting for Runtime event: $EventName"
}

function Wait-ForCoreReady([int]$MinimumGeneration = 1, [int]$TimeoutSeconds = 60) {
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        $matches = @((Read-RuntimeRecords) | Where-Object {
            $_.event -eq "core.readiness.reached" -and [int64]$_.generation_number -ge $MinimumGeneration
        })
        if ($matches.Count -ne 0) {
            $state = [string]$matches[-1].attributes.host_state
            if ($state -in @("ready", "degraded")) { return }
            if ($state -in @("setup_required", "failed")) {
                throw "Core reached an unusable acceptance state: $state"
            }
        }
        Start-Sleep -Milliseconds 200
    }
    throw "Timed out waiting for Core generation $MinimumGeneration."
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
    if (-not (Split-Path -Leaf $resolved).StartsWith("sakura-wp-4-04-manual-")) {
        throw "Acceptance cleanup target has an unexpected name: $resolved"
    }
}

Push-Location $repo
try {
    cargo build --locked --manifest-path desktop/src-tauri/Cargo.toml
    if ($LASTEXITCODE -ne 0) { throw "Failed to build the WP-4-04 debug candidate." }
    foreach ($required in @($python, $providerServer, $pluginSource)) {
        if (-not (Test-Path -LiteralPath $required)) { throw "Missing acceptance dependency: $required" }
    }

    $realDataBefore = Get-FileManifest (Join-Path $repo "data")
    $realPluginsBefore = Get-FileManifest (Join-Path $repo "plugins")
    New-Item -ItemType Directory -Path $appRoot -Force | Out-Null
    Copy-Item -Path (Join-Path $source "*") -Destination $appRoot -Recurse -Force
    Copy-Item -LiteralPath $pluginSource -Destination (Join-Path $appRoot "plugins") -Recurse -Force
    $fixturePortrait = Join-Path $appRoot "characters\fixture\portraits\placeholder.png"
    Copy-Item -LiteralPath (Join-Path $repo "desktop\src-tauri\icons\icon.png") -Destination $fixturePortrait
    $fixtureManifest = Join-Path $appRoot "characters\fixture\character.json"
    $manifestText = [IO.File]::ReadAllText($fixtureManifest).Replace("portraits/placeholder.txt", "portraits/placeholder.png")
    [IO.File]::WriteAllText($fixtureManifest, $manifestText, [Text.UTF8Encoding]::new($false))
    [IO.File]::WriteAllText(
        (Join-Path $appRoot "data\config\plugins.yaml"),
        "- id: fixture_plugin`n  enabled: true`n  priority: 100`n- id: broken_plugin`n  enabled: true`n  priority: 50`n",
        [Text.UTF8Encoding]::new($false)
    )
    [IO.File]::WriteAllText(
        (Join-Path $appRoot "data\plugins\fixture_plugin\config.json"),
        (@{ label = $settingsSentinel } | ConvertTo-Json),
        [Text.UTF8Encoding]::new($false)
    )

    $providerProcess = Start-Process -FilePath $python -ArgumentList @(
        $providerServer, "--port-file", $providerPortFile, "--tool-sentinel", $toolSentinel
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
    alias: WP-4-04 Local Provider
    base_url: http://127.0.0.1:$providerPort/v1
    api_key: LOCAL_WP404_KEY
    models:
      - name: fixture-model
model_slots:
  chat:
    profile_id: fixture
    model: fixture-model
config_version: 1
"@
    [IO.File]::WriteAllText((Join-Path $appRoot "data\config\api.yaml"), $apiConfig, [Text.UTF8Encoding]::new($false))

    Write-Host ""
    Write-Host "WP-4-04 Python 插件实机验收" -ForegroundColor Cyan
    Write-Host "隔离根：$appRoot"
    Write-Host ""
    Write-Host "请在 Sakura 中依次完成：" -ForegroundColor Yellow
    Write-Host "1. 打开插件设置，确认 Fixture Plugin 为 ready，Broken Plugin 为 degraded；页面不显示 entry 或文件路径。"
    Write-Host "2. 发送“测试插件成功”；确认工具直接执行，不出现权限或二次确认提示，且未显示“prompt/context 尚未生效”。"
    Write-Host "3. 在插件设置将 Label 改为可辨识值，执行 Reset，再编辑并保存；确认 Core 重启后页面值正确。"
    Write-Host "4. 禁用 Fixture Plugin 并保存；确认重启后为 disabled。再启用并保存；确认再次 ready。"
    Write-Host "5. 发送“测试插件超时”；确认出现稳定失败 reasonCode、当前插件贡献失效且应用仍可用。"
    Write-Host "6. 用任务管理器结束命令行含 app.core_host 且含隔离根的 python.exe；确认 Core 自动恢复且插件重新 ready。"
    Write-Host "7. 从菜单正常退出 Sakura。"
    Write-Host ""

    $oldManualRoot = $env:SAKURA_WP_4_01_MANUAL_ROOT
    $oldLogLevel = $env:SAKURA_RUNTIME_V2_LOG_LEVEL
    try {
        $env:SAKURA_WP_4_01_MANUAL_ROOT = $appRoot
        $env:SAKURA_RUNTIME_V2_LOG_LEVEL = "debug"
        $process = Start-Process -FilePath $candidate -PassThru
        Wait-ForRuntimeEvent "shell.ready"
        Wait-ForCoreReady
        $process.WaitForExit()
        if ($process.ExitCode -ne 0) { throw "WP-4-04 candidate exited with code $($process.ExitCode)." }
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
        throw "WP-4-04 left related Shell/Core/plugin processes: $($related | ConvertTo-Json -Compress)"
    }

    $logs = Read-RuntimeLogs
    foreach ($forbidden in @($toolSentinel, $settingsSentinel, $appRoot, $providerServer, $pluginSource)) {
        if ($logs.Contains($forbidden)) { throw "Private plugin value reached persistent logs: $forbidden" }
    }
    $records = Read-RuntimeRecords
    $events = @($records | ForEach-Object { $_.event })
    foreach ($requiredEvent in @("webview.settings.opened", "webview.chat.send", "shell.stopped")) {
        if ($requiredEvent -notin $events) { throw "Required plugin acceptance event was not found: $requiredEvent" }
    }
    $generations = @($records | Where-Object { $_.generation_number } | ForEach-Object {
        [int64]$_.generation_number
    } | Sort-Object -Unique)
    if ($generations.Count -lt 4) {
        throw "Plugin save/disable/enable/Core recovery did not produce at least four generations."
    }
    $pluginConfig = Get-Content -LiteralPath (Join-Path $appRoot "data\plugins\fixture_plugin\config.json") -Raw | ConvertFrom-Json
    if ($pluginConfig.event_role -ne "user" -or [int]$pluginConfig.event_characters -le 0) {
        throw "Plugin message summary event was not persisted by the fixture."
    }

    $realDataChanged = Compare-FileManifest $realDataBefore (Get-FileManifest (Join-Path $repo "data"))
    $realPluginsChanged = Compare-FileManifest $realPluginsBefore (Get-FileManifest (Join-Path $repo "plugins"))
    if ($realDataChanged.Count -ne 0) { throw "WP-4-04 changed repository data: $($realDataChanged -join ', ')" }
    if ($realPluginsChanged.Count -ne 0) { throw "WP-4-04 changed repository plugins: $($realPluginsChanged -join ', ')" }

    Assert-IsolatedAcceptanceRoot $acceptance
    Remove-Item -LiteralPath $acceptance -Recurse -Force
    if (Test-Path -LiteralPath $acceptance) { throw "WP-4-04 isolated acceptance root was not removed." }
    [ordered]@{
        status = "manual_session_completed"
        candidate = $candidate
        generations_observed = $generations
        sensitive_log_hits = 0
        related_process_residue = 0
        real_data_changed = @()
        real_plugins_changed = @()
        acceptance_root_removed = $true
        next = "Return the checklist result to the project owner; this script does not self-accept WP-4-04."
    } | ConvertTo-Json -Compress
}
finally {
    if ($providerProcess -and -not $providerProcess.HasExited) {
        Stop-Process -Id $providerProcess.Id -Force
        $providerProcess.WaitForExit()
    }
    Pop-Location
}
