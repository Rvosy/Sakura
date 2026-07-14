@echo off
chcp 65001 > nul
set "PRJ_ROOT=%~dp0"
set "SAKURA_PRJ_ROOT=%PRJ_ROOT%"

REM ============================================================
REM 检测 Python：只使用 runtime/python.exe
REM ============================================================
if exist "%PRJ_ROOT%\runtime\python.exe" (
    set "PYTHON_EXE=%PRJ_ROOT%\runtime\python.exe"
) else (
    echo [错误] 未找到 runtime\python.exe，请先准备 runtime 目录
    pause
    exit /b 1
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
"%PYTHON_EXE%" main.py
pause
