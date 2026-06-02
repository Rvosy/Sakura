#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# ============================================================
# 检测 Python：优先使用 runtime 内置 Python，其次系统 Python
# ============================================================
if [ -f "$PROJECT_ROOT/runtime/bin/python3" ]; then
    PYTHON_EXE="$PROJECT_ROOT/runtime/bin/python3"
elif [ -f "$PROJECT_ROOT/runtime/python.exe" ]; then
    PYTHON_EXE="$PROJECT_ROOT/runtime/python.exe"
else
    if ! command -v python3 &> /dev/null; then
        echo "[错误] 未检测到 Python3，请先运行 scripts/install.sh 安装依赖"
        exit 1
    fi
    PYTHON_EXE="python3"
fi

# ============================================================
# 设置 sentence-transformers 模型缓存到项目目录
# ============================================================
export HF_HOME="$PROJECT_ROOT/runtime/hf-cache"
export SENTENCE_TRANSFORMERS_HOME="$PROJECT_ROOT/runtime/hf-cache"
mkdir -p "$HF_HOME"

# ============================================================
# 启动
# ============================================================
cd "$PROJECT_ROOT"
exec $PYTHON_EXE main.py
