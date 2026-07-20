@echo off
chcp 65001 > nul
set "PRJ_ROOT=%~dp0"
set "SAKURA_PRJ_ROOT=%PRJ_ROOT%"

cd /d "%PRJ_ROOT%"
set "TAURI_EXE=%PRJ_ROOT%\desktop\src-tauri\target\release\sakura-runtime-v2-shell.exe"
if not exist "%TAURI_EXE%" set "TAURI_EXE=%PRJ_ROOT%\desktop\src-tauri\target\debug\sakura-runtime-v2-shell.exe"
if not exist "%TAURI_EXE%" (
    echo [错误] 未找到 Sakura Runtime v2 Tauri Shell，请先构建 desktop\src-tauri
    exit /b 1
)
"%TAURI_EXE%" %*
exit /b %errorlevel%
