#!/bin/bash
set -e

echo "========================================"
echo "  Sakura 依赖安装"
echo "========================================"
echo ""

# ============================================================
# 检测 Python：优先使用 runtime 内置 Python，其次系统 Python
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
    echo "[提示] 未找到内置 Python，尝试查找系统 Python3..."
    if ! command -v python3 &> /dev/null; then
        echo "[错误] 未检测到 Python3，请安装 Python 或下载完整 release 包"
        echo "        https://www.python.org/downloads/"
        exit 1
    fi
    PYTHON_EXE="python3"
    echo "[OK] 使用系统 Python3"
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
$PYTHON_EXE -m pip install -r requirements.txt

echo ""
echo "========================================"
echo "  安装完成！运行 scripts/start.sh 启动"
echo "========================================"
