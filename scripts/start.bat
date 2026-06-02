@echo off
chcp 65001 > nul

REM ============================================================
REM 检测非 ASCII 路径（PySide6 在非英文路径下会崩溃）
REM ============================================================
pushd %~dp0..
set "PRJ_ROOT=%cd%"
popd
powershell -Command "if ('%PRJ_ROOT%' -match '[^\x20-\x7E]') { Write-Host '[错误] 项目路径包含非英文字符，PySide6 无法正常启动'; Write-Host '       请将项目移动到纯英文路径，如 D:\sakura'; Write-Host '       当前路径: %PRJ_ROOT%'; exit 1 } else { exit 0 }" > nul 2>&1
if %errorlevel% neq 0 (
    powershell -Command "Write-Host '[错误] 项目路径包含非英文字符，PySide6 无法正常启动'; Write-Host '       请将项目移动到纯英文路径，如 D:\sakura'; Write-Host '       当前路径: %PRJ_ROOT%'"
    pause
    exit /b 1
)

REM ============================================================
REM 检测 Python：优先使用 runtime/python.exe，其次系统 Python
REM ============================================================
if exist "runtime\python.exe" (
    set "PYTHON_EXE=%~dp0..\runtime\python.exe"
) else (
    where python > nul 2>&1
    if %errorlevel% neq 0 (
        echo [错误] 未检测到 Python，请先运行 scripts\install.bat 安装依赖
        pause
        exit /b 1
    )
    set "PYTHON_EXE=python"
)

REM ============================================================
REM 设置 sentence-transformers 模型缓存到项目目录
REM ============================================================
set "HF_HOME=%PRJ_ROOT%\runtime\hf-cache"
set "SENTENCE_TRANSFORMERS_HOME=%PRJ_ROOT%\runtime\hf-cache"
if not exist "%HF_HOME%" mkdir "%HF_HOME%"

REM ============================================================
REM 启动
REM ============================================================
cd /d "%PRJ_ROOT%"
%PYTHON_EXE% main.py
pause
