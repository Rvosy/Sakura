@echo off
chcp 65001 > nul
for %%I in ("%~dp0..") do set "PRJ_ROOT=%%~fI"

cd /d "%PRJ_ROOT%"
call cargo build --manifest-path "%PRJ_ROOT%\desktop\src-tauri\Cargo.toml" --locked
if errorlevel 1 (
    echo [错误] Sakura Runtime v2 开发版编译失败。
    exit /b 1
)

set "TAURI_EXE=%PRJ_ROOT%\desktop\src-tauri\target\debug\sakura.exe"
if not exist "%TAURI_EXE%" (
    echo [错误] 编译完成后仍未找到 Sakura Runtime v2 Tauri Shell。
    exit /b 1
)
"%TAURI_EXE%" %*
exit /b %errorlevel%
