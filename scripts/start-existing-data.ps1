param(
    [string]$UserRoot = (Join-Path $env:LOCALAPPDATA 'Sakura Development')
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

# This launch uses the original data directly; it does not prepare a new profile.
$acceptancePreviousRoot = $env:SAKURA_RUNTIME_USER_ROOT
$acceptancePreviousLocation = Get-Location
try {
    $env:SAKURA_RUNTIME_USER_ROOT = $acceptanceUserRoot
    Set-Location -LiteralPath $acceptanceRepository
    Write-Host ('正在使用已有数据：' + $acceptanceUserRoot)
    & $acceptanceExecutable
    $acceptanceExitCode = $LASTEXITCODE
} finally {
    $env:SAKURA_RUNTIME_USER_ROOT = $acceptancePreviousRoot
    Set-Location -LiteralPath $acceptancePreviousLocation
}
if ($null -ne $acceptanceExitCode) { exit $acceptanceExitCode }
