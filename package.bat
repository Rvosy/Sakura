@echo off
chcp 65001 > nul
set "PROJECT_ROOT=%~dp0"
pushd "%PROJECT_ROOT%"

where pwsh >nul 2>nul
if not errorlevel 1 (
    pwsh -NoProfile -ExecutionPolicy Bypass -File "%PROJECT_ROOT%scripts\package_windows.ps1" %*
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%PROJECT_ROOT%scripts\package_windows.ps1" %*
)

if errorlevel 1 (
    echo.
    echo [错误] 打包失败，请检查上方日志。
    popd
    exit /b 1
)

echo.
echo [完成] 安装包已生成到 artifacts\local
popd
