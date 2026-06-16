@echo off
chcp 65001 > nul
set "PRJ_ROOT=%~dp0"
set "SAKURA_PRJ_ROOT=%PRJ_ROOT%"

powershell -NoProfile -Command "$path = $env:SAKURA_PRJ_ROOT; if ($path -match '[^\x20-\x7E]') { exit 1 } else { exit 0 }" > nul 2>&1
if errorlevel 1 (
    powershell -NoProfile -Command "Write-Host '[ERROR] Non-ASCII path detected. Please move the project to a pure ASCII path such as D:\sakura'; Write-Host ('Current path: ' + $env:SAKURA_PRJ_ROOT)"
    pause
    exit /b 1
)

if exist "%PRJ_ROOT%runtime\python.exe" (
    set "PYTHON_EXE=%PRJ_ROOT%runtime\python.exe"
) else (
    where python > nul 2>&1
    if errorlevel 1 (
        echo [ERROR] Python not found. Please run install.bat first.
        pause
        exit /b 1
    )
    set "PYTHON_EXE=python"
)

set "HF_HOME=%PRJ_ROOT%runtime\hf-cache"
set "SENTENCE_TRANSFORMERS_HOME=%PRJ_ROOT%runtime\hf-cache"
if not exist "%HF_HOME%" mkdir "%HF_HOME%"

cd /d "%PRJ_ROOT%"
"%PYTHON_EXE%" -m tools.studio.main 2> "%PRJ_ROOT%studio_error.log"
if errorlevel 1 (
    echo.
    echo [ERROR] SakuraCharacterStudio exited with error:
    echo --------------------------------------------------------
    type "%PRJ_ROOT%studio_error.log"
    echo --------------------------------------------------------
)
pause
