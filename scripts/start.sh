#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# ============================================================
# 检测 Python：只使用 runtime 内置 Python
# ============================================================
if [ -f "$PROJECT_ROOT/runtime/bin/python3" ]; then
    PYTHON_EXE="$PROJECT_ROOT/runtime/bin/python3"
elif [ -f "$PROJECT_ROOT/runtime/python.exe" ]; then
    PYTHON_EXE="$PROJECT_ROOT/runtime/python.exe"
else
    echo "[错误] 未找到 runtime 内置 Python，请先准备 runtime 目录"
    exit 1
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
exec "$PYTHON_EXE" main.py
