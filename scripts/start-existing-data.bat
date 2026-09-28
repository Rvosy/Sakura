@echo off
chcp 65001 >nul
setlocal DisableDelayedExpansion
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-existing-data.ps1" %*
set "SAKURA_LAUNCH_EXIT_CODE=%errorlevel%"
if errorlevel 1 pause
exit /b %SAKURA_LAUNCH_EXIT_CODE%
