@echo off
chcp 65001 > nul
set "PRJ_ROOT=%~dp0"
set "SAKURA_PRJ_ROOT=%PRJ_ROOT%"

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
    echo         请前往 GitHub Releases 下载包含 runtime 的完整包:
    echo         https://github.com/Rvosy/sakura/releases
    pause
    exit /b 1
)

REM ============================================================
REM 检测非 ASCII 路径（PySide6 在非英文路径下会崩溃）
REM ============================================================
powershell -NoProfile -Command "$path = $env:SAKURA_PRJ_ROOT; if ($path -match '[^\x20-\x7E]') { exit 1 } else { exit 0 }" > nul 2>&1
if errorlevel 1 (
    powershell -NoProfile -Command "$path = $env:SAKURA_PRJ_ROOT; Write-Host '[错误] 项目路径包含非英文字符，PySide6 无法正常启动'; Write-Host '       请将项目移动到纯英文路径，如 D:\sakura'; Write-Host ('       当前路径: ' + $path)"
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
echo [1/2] 安装 Python 依赖...
echo.

"%PYTHON_EXE%" -m pip install -r "%PRJ_ROOT%\requirements.txt" -i https://mirrors.aliyun.com/pypi/simple --extra-index-url https://pypi.tuna.tsinghua.edu.cn/simple --extra-index-url https://pypi.org/simple --no-warn-script-location

if errorlevel 1 (
    echo.
    echo [错误] 依赖安装失败，请检查网络连接后重试
    pause
    exit /b 1
)

echo.
echo [2/2] 验证关键依赖...
"%PYTHON_EXE%" -c "import PySide6; import playwright; print('[OK] PySide6 + Playwright 就绪')"
if errorlevel 1 (
    echo [警告] 部分依赖验证失败，但安装过程已完成，请检查上方输出
)

echo.
echo ========================================
echo   安装完成！双击 start.bat 启动
echo ========================================
pause
