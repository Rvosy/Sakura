#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# ============================================================
# 启动已构建的 Tauri Shell
# ============================================================
cd "$PROJECT_ROOT"
for PROFILE in release debug; do
    TAURI_SHELL="$PROJECT_ROOT/desktop/src-tauri/target/$PROFILE/sakura-runtime-v2-shell"
    if [ -x "$TAURI_SHELL" ]; then
        exec "$TAURI_SHELL" "$@"
    fi
done

echo "[错误] 未找到 Tauri Shell。请先构建 desktop/src-tauri（debug 或 release）。" >&2
exit 1
