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

if [ ! -f "$PROJECT_ROOT/runtime/bin/python3" ] && [ ! -f "$PROJECT_ROOT/runtime/python.exe" ]; then
    if ! command -v python3 >/dev/null 2>&1; then
        echo "[错误] 未找到 runtime 内置 Python，且系统没有 python3，无法下载冻结 Runtime"
        exit 1
    fi
    echo "未找到 runtime 内置 Python，正在按当前平台清单下载冻结 Runtime..."
    python3 "$SCRIPT_DIR/bootstrap_runtime.py"
fi

if [ -f "$PROJECT_ROOT/runtime/bin/python3" ]; then
    PYTHON_EXE="$PROJECT_ROOT/runtime/bin/python3"
    echo "[OK] 找到 runtime/bin/python3"
elif [ -f "$PROJECT_ROOT/runtime/python.exe" ]; then
    PYTHON_EXE="$PROJECT_ROOT/runtime/python.exe"
    echo "[OK] 找到 runtime/python.exe"
else
    echo "[错误] 未找到 runtime 内置 Python"
    echo "        请前往 GitHub Releases 下载包含 runtime 的完整包，或重试 scripts/bootstrap_runtime.py:"
    echo "        https://github.com/Rvosy/sakura/releases"
    exit 1
fi

if [ "$(uname -s)" = "Linux" ]; then
    if ! pkg-config --exists webkit2gtk-4.1 gtk+-3.0 2>/dev/null; then
        echo "[错误] 未找到 WebKitGTK 4.1 / GTK 3 开发文件，无法编译 Tauri Shell"
        echo "        Ubuntu 24.04 可安装："
        echo "        sudo apt-get update"
        echo "        sudo apt-get install -y build-essential pkg-config clang libclang-dev libssl-dev \\"
        echo "          libasound2-dev libgbm-dev libpipewire-0.3-dev libwebkit2gtk-4.1-dev \\"
        echo "          libayatana-appindicator3-dev libxdo-dev librsvg2-dev"
        echo "        完整步骤见 docs/userdocs/LINUX_SETUP.md"
        exit 1
    fi
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
export PIP_INDEX_URL="${PIP_INDEX_URL:-https://mirrors.aliyun.com/pypi/simple}"
"$PYTHON_EXE" -m pip install -r requirements.txt --no-warn-script-location

echo ""
echo "Preparing isolated dependencies for bundled plugins..."
"$PYTHON_EXE" tools/development_plugin_dependencies.py

echo ""
echo "========================================"
echo "  安装完成！运行 scripts/start.sh 启动"
echo "========================================"
if [ "$(uname -s)" = "Linux" ]; then
    echo "Linux 说明见 docs/userdocs/LINUX_SETUP.md"
fi
