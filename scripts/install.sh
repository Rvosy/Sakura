#!/bin/bash
set -e

echo "========================================"
echo "  Sakura 依赖安装"
echo "========================================"
echo ""

# ============================================================
# 检测 Python：只使用 runtime 内置 Python
# ============================================================
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

if [ -f "$PROJECT_ROOT/runtime/bin/python3" ]; then
    PYTHON_EXE="$PROJECT_ROOT/runtime/bin/python3"
    echo "[OK] 找到 runtime/bin/python3"
elif [ -f "$PROJECT_ROOT/runtime/python.exe" ]; then
    PYTHON_EXE="$PROJECT_ROOT/runtime/python.exe"
    echo "[OK] 找到 runtime/python.exe"
else
    echo "[错误] 未找到 runtime 内置 Python"
    echo "        请前往 GitHub Releases 下载包含 runtime 的完整包:"
    echo "        https://github.com/Rvosy/sakura/releases"
    exit 1
fi

# ============================================================
# 检测 requirements.txt
# ============================================================
if [ ! -f "$PROJECT_ROOT/requirements.txt" ]; then
    echo "[错误] 未找到 requirements.txt"
    exit 1
fi

# ============================================================
# pip install 依赖
# ============================================================
echo ""
echo "Installing dependencies..."
echo ""

cd "$PROJECT_ROOT"
"$PYTHON_EXE" -m pip install -r requirements.txt --no-warn-script-location

echo ""
echo "========================================"
echo "  安装完成！运行 scripts/start.sh 启动"
echo "========================================"
