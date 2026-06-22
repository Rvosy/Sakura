@echo off
chcp 65001 > nul
set "PRJ_ROOT=%~dp0"
set "PYTHON_EXE=%PRJ_ROOT%runtime\python.exe"

if not exist "%PYTHON_EXE%" (
    echo [ERROR] runtime\python.exe not found.
    echo         Download the full Sakura package from GitHub Releases and try again.
    pause
    exit /b 1
)

cd /d "%PRJ_ROOT%"
"%PYTHON_EXE%" "%PRJ_ROOT%tools\update.py" %*
set "EXIT_CODE=%ERRORLEVEL%"
pause
exit /b %EXIT_CODE%
