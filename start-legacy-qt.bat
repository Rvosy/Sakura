@echo off
chcp 65001 > nul
set "PRJ_ROOT=%~dp0"
if not exist "%PRJ_ROOT%\runtime\python.exe" (
    echo [错误] 未找到 runtime\python.exe，请先准备 runtime 目录
    exit /b 1
)
cd /d "%PRJ_ROOT%"
"%PRJ_ROOT%\runtime\python.exe" legacy_qt_main.py %*
exit /b %errorlevel%
