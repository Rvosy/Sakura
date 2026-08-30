[CmdletBinding()]
param(
    [string]$CacheDirectory = "",
    [string]$OutputDirectory = "",
    [switch]$KeepStaging,
    [switch]$Updater,
    [switch]$UpdaterArtifacts
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$python = Join-Path $projectRoot "runtime\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "未找到 runtime\python.exe；请先准备仓库的 bundled Python Runtime。"
}
$version = (& $python (Join-Path $projectRoot "tools\release\versioning.py")).Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($version)) {
    throw "无法读取发行版本。"
}

if ([string]::IsNullOrWhiteSpace($CacheDirectory)) {
    $CacheDirectory = Join-Path $projectRoot "temp\release-cache"
}
if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $OutputDirectory = Join-Path $projectRoot "artifacts\local"
}
$cacheRoot = [IO.Path]::GetFullPath($CacheDirectory)
$outputRoot = [IO.Path]::GetFullPath($OutputDirectory)
$buildRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot "temp\package-build\windows-x64"))
$releaseStage = [IO.Path]::GetFullPath((Join-Path $projectRoot "desktop\src-tauri\release-staging"))
$portableStage = [IO.Path]::GetFullPath((Join-Path $projectRoot "desktop\src-tauri\portable-staging"))
$tauriStageRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot "desktop\src-tauri")).TrimEnd('\') + '\'

function Assert-SafeBuildPath([string]$Path) {
    $allowed = [IO.Path]::GetFullPath((Join-Path $projectRoot "temp\package-build")).TrimEnd('\') + '\'
    if (-not $Path.StartsWith($allowed, [StringComparison]::OrdinalIgnoreCase)) {
        throw "拒绝清理非构建目录：$Path"
    }
}

function Remove-BuildDirectory([string]$Path) {
    Assert-SafeBuildPath $Path
    if (Test-Path -LiteralPath $Path) {
        Remove-Item -LiteralPath $Path -Recurse -Force
    }
}

function Remove-StageDirectory([string]$Path) {
    if (-not $Path.StartsWith($tauriStageRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "拒绝清理非 Tauri staging 目录：$Path"
    }
    if ([IO.Path]::GetFileName($Path) -notin @("release-staging", "portable-staging")) {
        throw "拒绝清理未知 Tauri 目录：$Path"
    }
    if (Test-Path -LiteralPath $Path) {
        Remove-Item -LiteralPath $Path -Recurse -Force
    }
}

function Invoke-Checked([string]$Program, [string[]]$Arguments) {
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "命令失败（exit $LASTEXITCODE）：$Program $($Arguments -join ' ')"
    }
}

New-Item -ItemType Directory -Path $cacheRoot -Force | Out-Null
New-Item -ItemType Directory -Path $outputRoot -Force | Out-Null
$env:PIP_CACHE_DIR = Join-Path $cacheRoot "pip"
$env:UV_CACHE_DIR = Join-Path $cacheRoot "uv"
$env:PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD = "1"
$env:PYTHONUTF8 = "1"
if ($Updater -and $UpdaterArtifacts) {
    throw "-Updater 与 -UpdaterArtifacts 不能同时使用。"
}
if ($version -notmatch '-' -and -not $Updater -and -not $UpdaterArtifacts) {
    throw "稳定版安装包必须使用 -Updater 或 -UpdaterArtifacts，拒绝生成无法检测更新的正式版本产物。"
}
if (($Updater -or $UpdaterArtifacts) -and [string]::IsNullOrWhiteSpace($env:SAKURA_UPDATER_PUBLIC_KEY)) {
    throw "Updater 构建需要环境变量 SAKURA_UPDATER_PUBLIC_KEY。"
}
if ($UpdaterArtifacts -and
    [string]::IsNullOrWhiteSpace($env:TAURI_SIGNING_PRIVATE_KEY) -and
    [string]::IsNullOrWhiteSpace($env:TAURI_SIGNING_PRIVATE_KEY_PATH)) {
    throw "-UpdaterArtifacts 需要 TAURI_SIGNING_PRIVATE_KEY 或 TAURI_SIGNING_PRIVATE_KEY_PATH。"
}
$privateKeyLoadedFromPath = $false

$manifestPath = Join-Path $projectRoot "desktop\src-tauri\runtime-layouts\windows-x64\runtime-manifest.json"
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
$archivePath = Join-Path (Join-Path $cacheRoot "downloads") ([string]$manifest.archive.fileName)
New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($archivePath)) -Force | Out-Null

$archiveArguments = @(
    (Join-Path $projectRoot "scripts\runtime_v2_archive.py"),
    "--manifest", $manifestPath,
    "--output", $archivePath
)
$hadCachedArchive = Test-Path -LiteralPath $archivePath -PathType Leaf
try {
    Invoke-Checked $python $archiveArguments
}
catch {
    if (-not $hadCachedArchive) { throw }
    Write-Warning "缓存的 Python 归档校验失败，删除该缓存后重新下载。"
    Remove-Item -LiteralPath $archivePath -Force
    Invoke-Checked $python $archiveArguments
}

Remove-BuildDirectory $buildRoot
New-Item -ItemType Directory -Path $buildRoot | Out-Null
$pythonRoot = Join-Path $buildRoot "python-runtime"
New-Item -ItemType Directory -Path $pythonRoot | Out-Null

try {
    Expand-Archive -LiteralPath $archivePath -DestinationPath $pythonRoot
    Push-Location $projectRoot
    try {
        Invoke-Checked $python @(
            "-m", "tools.release.prepare_python_runtime",
            "--target", "windows-x64",
            "--python-root", $pythonRoot,
            "--lock", (Join-Path $projectRoot "packaging\requirements-windows-x64.lock")
        )
    }
    finally {
        Pop-Location
    }

    foreach ($stage in @($releaseStage, $portableStage)) {
        Remove-StageDirectory $stage
    }

    Invoke-Checked $python @(
        (Join-Path $projectRoot "tools\release\stage_distribution.py"),
        "--target", "windows-x64",
        "--python-root", $pythonRoot,
        "--output", $releaseStage,
        "--smoke"
    )
    $tauriConfigArguments = @(
        (Join-Path $projectRoot "tools\release\tauri_release_config.py"),
        "--target", "windows-x64",
        "--output", (Join-Path $projectRoot "desktop\src-tauri\tauri.release.json")
    )
    if ($Updater) {
        $tauriConfigArguments += "--updater-client"
    }
    elseif ($UpdaterArtifacts) {
        $tauriConfigArguments += "--updater"
    }
    Invoke-Checked $python $tauriConfigArguments

    Push-Location (Join-Path $projectRoot "desktop\src-tauri")
    try {
        if ($UpdaterArtifacts -and [string]::IsNullOrWhiteSpace($env:TAURI_SIGNING_PRIVATE_KEY)) {
            $privateKeyPath = [IO.Path]::GetFullPath($env:TAURI_SIGNING_PRIVATE_KEY_PATH)
            if (-not (Test-Path -LiteralPath $privateKeyPath -PathType Leaf)) {
                throw "TAURI_SIGNING_PRIVATE_KEY_PATH 指向的私钥文件不存在。"
            }
            # Tauri CLI 2.11.4 的 updater bundler 实际读取私钥内容变量；
            # 本地脚本仍接受更安全、便于使用的路径变量，并只在当前子进程中展开。
            $env:TAURI_SIGNING_PRIVATE_KEY = Get-Content -Raw -LiteralPath $privateKeyPath
            $privateKeyLoadedFromPath = $true
        }
        Invoke-Checked "npx" @("--yes", "@tauri-apps/cli@2.11.4", "build", "--config", "tauri.release.json")
    }
    finally {
        Pop-Location
    }

    $installer = Get-ChildItem -LiteralPath (Join-Path $projectRoot "desktop\src-tauri\target\release\bundle\nsis") -Filter "*.exe" |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if ($null -eq $installer) { throw "Tauri 未生成 NSIS 安装包。" }
    $setup = Join-Path $outputRoot "Sakura-$version-windows-x64-setup.exe"
    Copy-Item -LiteralPath $installer.FullName -Destination $setup -Force
    $updaterArtifact = $null
    $updaterSignature = $null
    if ($UpdaterArtifacts) {
        $generatedSignature = "$($installer.FullName).sig"
        if (-not (Test-Path -LiteralPath $generatedSignature -PathType Leaf)) {
            throw "Tauri 未生成签名的 NSIS updater 产物。"
        }
        # Tauri v2 updater directly downloads the signed NSIS installer. The
        # legacy .nsis.zip shape is only produced by "v1Compatible" mode.
        $updaterArtifact = $setup
        $updaterSignature = "$setup.sig"
        Copy-Item -LiteralPath $generatedSignature -Destination $updaterSignature -Force
    }

    Invoke-Checked $python @(
        (Join-Path $projectRoot "tools\release\stage_distribution.py"),
        "--target", "windows-x64",
        "--python-root", $pythonRoot,
        "--output", $portableStage,
        "--portable",
        "--smoke"
    )
    $shell = Join-Path $projectRoot "desktop\src-tauri\target\release\sakura.exe"
    Copy-Item -LiteralPath $shell -Destination (Join-Path $portableStage "sakura.exe") -Force
    Remove-Item -LiteralPath (Join-Path $portableStage "release-inventory.json") -Force
    $portable = Join-Path $outputRoot "Sakura-$version-windows-x64-portable.zip"
    if (Test-Path -LiteralPath $portable) { Remove-Item -LiteralPath $portable -Force }
    Compress-Archive -Path (Join-Path $portableStage "*") -DestinationPath $portable -CompressionLevel Optimal

    $plugin = Join-Path $outputRoot "Sakura-Playwright-$version.sakplugin.zip"
    if (Test-Path -LiteralPath $plugin) { Remove-Item -LiteralPath $plugin -Force }
    Invoke-Checked $python @(
        (Join-Path $projectRoot "tools\release\package_optional_plugin.py"),
        "--source", (Join-Path $projectRoot "plugins\optional\playwright_browser"),
        "--output", $plugin
    )
    $reportArguments = @(
        (Join-Path $projectRoot "tools\release\artifact_report.py"),
        "--inventory", (Join-Path $releaseStage "release-inventory.json"),
        "--output", (Join-Path $outputRoot "windows-x64-size-report.json"),
        "--installed-path", $portableStage,
        "--artifact", $setup,
        "--artifact", $portable
    )
    Invoke-Checked $python $reportArguments

    Write-Host ""
    Write-Host "打包完成：$outputRoot"
    $outputs = @($setup, $portable, $plugin)
    if ($null -ne $updaterArtifact) {
        $outputs += $updaterSignature
    }
    Get-Item -LiteralPath $outputs | Select-Object Name, Length
}
finally {
    if ($privateKeyLoadedFromPath) {
        Remove-Item Env:TAURI_SIGNING_PRIVATE_KEY -ErrorAction SilentlyContinue
    }
    Remove-BuildDirectory $buildRoot
    if (-not $KeepStaging) {
        foreach ($stage in @($releaseStage, $portableStage)) {
            Remove-StageDirectory $stage
        }
    }
}
