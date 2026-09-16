@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo Sakura Site
echo.

if not exist "node_modules\" (
    echo Installing deps...
    call pnpm install
    if errorlevel 1 (
        echo Install failed. pnpm installed?
        pause
        exit /b 1
    )
)

echo Starting dev server...
echo http://localhost:4321
echo Ctrl+C to stop
echo.

call pnpm dev
pause
