@echo off
chcp 65001 > nul
set "PRJ_ROOT=%~dp0"

echo ========================================
echo   Sakura 依赖安装
echo ========================================
echo.

REM ============================================================
REM 检测 Python：只使用 runtime/python.exe
REM ============================================================
if exist "%PRJ_ROOT%\runtime\python.exe" (
    set "PYTHON_EXE=%PRJ_ROOT%\runtime\python.exe"
    echo [OK] 找到 runtime\python.exe
) else (
    echo [错误] 未找到 runtime\python.exe
    echo         请前往 GitHub Releases 下载 runtime 运行时文件加入目录:
    echo         https://github.com/Rvosy/sakura/releases
    pause
    exit /b 1
)

REM ============================================================
REM 检测 requirements.txt
REM ============================================================
if not exist "%PRJ_ROOT%\requirements.txt" (
    echo [错误] 未找到 requirements.txt
    pause
    exit /b 1
)

REM ============================================================
REM pip install 依赖（优先国内镜像）
REM ============================================================
echo.
echo [1/3] 安装 Core Python 依赖...
echo.

"%PYTHON_EXE%" -m pip install -r "%PRJ_ROOT%\requirements.txt" -i https://mirrors.aliyun.com/pypi/simple --extra-index-url https://pypi.tuna.tsinghua.edu.cn/simple --no-warn-script-location

if errorlevel 1 (
    echo.
    echo [错误] 依赖安装失败，请检查网络连接后重试
    pause
    exit /b 1
)

echo.
echo [2/3] 准备官方插件隔离依赖...
"%PYTHON_EXE%" "%PRJ_ROOT%\tools\development_plugin_dependencies.py"
if errorlevel 1 (
    echo [错误] 插件隔离依赖安装失败，请检查网络连接后重试
    pause
    exit /b 1
)

echo.
echo [3/3] 验证 Core 关键依赖...
"%PYTHON_EXE%" -c "import mcp; import yaml; print('[OK] Core 依赖就绪')"
if errorlevel 1 (
    echo [警告] 部分依赖验证失败，但安装过程已完成，请检查上方输出
)

echo.
echo ========================================
echo   安装完成！双击 start.bat 启动
echo ========================================
pause
