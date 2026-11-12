@echo off
chcp 65001 > nul
for %%I in ("%~dp0..") do set "PRJ_ROOT=%%~fI"

cd /d "%PRJ_ROOT%"
call cargo run --manifest-path "%PRJ_ROOT%\desktop\src-tauri\Cargo.toml" --locked -- %*
exit /b %errorlevel%
