@echo off
chcp 65001 > nul
set "PRJ_ROOT=%~dp0.."
cd /d "%PRJ_ROOT%\tools\settings-tauri"

echo ========== Building Tauri settings window (sakura-settings.exe) ==========
cargo build --release --manifest-path src-tauri\Cargo.toml

if errorlevel 1 (
    echo FAILED -- check Rust/Cargo environment.
    pause
    exit /b 1
)
echo SUCCESS -- output: src-tauri\target\release\sakura-settings.exe
pause