param(
    [string]$UserRoot = (Join-Path $env:LOCALAPPDATA 'Sakura Development'),
    [switch]$UseEnvironmentProxy
)

$ErrorActionPreference = 'Stop'
$acceptanceRepository = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$acceptanceExecutable = Join-Path $acceptanceRepository 'desktop\src-tauri\target\debug\sakura.exe'

if (-not (Test-Path -LiteralPath $UserRoot -PathType Container)) {
    Write-Host ('找不到已有数据目录：' + $UserRoot)
    exit 1
}
$acceptanceUserRoot = (Resolve-Path -LiteralPath $UserRoot).Path
if (-not (Test-Path -LiteralPath $acceptanceExecutable -PathType Leaf)) {
    Write-Host '尚未找到 Sakura 开发版，请先运行 scripts\start.bat 编译。'
    exit 1
}
if (@(Get-Process -Name 'sakura' -ErrorAction SilentlyContinue).Count -gt 0) {
    Write-Host '请先从托盘菜单退出正在运行的 Sakura，再启动已有数据验收。'
    exit 1
}

# This desktop entry follows the current Windows proxy settings by default.
# Keep inherited proxy overrides only when explicitly requested; stale terminal
# values would otherwise shadow the system proxy in both Shell and plugins.
$acceptanceStartInfo = [System.Diagnostics.ProcessStartInfo]::new()
$acceptanceStartInfo.FileName = $acceptanceExecutable
$acceptanceStartInfo.WorkingDirectory = $acceptanceRepository
$acceptanceStartInfo.UseShellExecute = $false
$acceptanceStartInfo.EnvironmentVariables['SAKURA_RUNTIME_USER_ROOT'] = $acceptanceUserRoot
if (-not $UseEnvironmentProxy) {
    foreach ($acceptanceProxyName in @('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY')) {
        $acceptanceStartInfo.EnvironmentVariables.Remove($acceptanceProxyName)
        $acceptanceStartInfo.EnvironmentVariables.Remove($acceptanceProxyName.ToLowerInvariant())
    }
}

# This launch uses the original data directly; it does not prepare a new profile.
Write-Host ('正在使用已有数据：' + $acceptanceUserRoot)
$acceptanceProcess = [System.Diagnostics.Process]::Start($acceptanceStartInfo)
try {
    $acceptanceProcess.WaitForExit()
    $acceptanceExitCode = $acceptanceProcess.ExitCode
} finally {
    $acceptanceProcess.Dispose()
}
exit $acceptanceExitCode
