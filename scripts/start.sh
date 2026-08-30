#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
MACOS_WRAPPER_PLIST_TEMP=""
MACOS_WRAPPER_EXECUTABLE_TEMP=""
MACOS_WRAPPER_ICON_TEMP=""

cleanup_macos_wrapper_temporaries() {
    if [ -n "$MACOS_WRAPPER_PLIST_TEMP" ]; then
        /bin/rm -f -- "$MACOS_WRAPPER_PLIST_TEMP" 2>/dev/null || true
    fi
    if [ -n "$MACOS_WRAPPER_EXECUTABLE_TEMP" ]; then
        /bin/rm -f -- "$MACOS_WRAPPER_EXECUTABLE_TEMP" 2>/dev/null || true
    fi
    if [ -n "$MACOS_WRAPPER_ICON_TEMP" ]; then
        /bin/rm -f -- "$MACOS_WRAPPER_ICON_TEMP" 2>/dev/null || true
    fi
}

fail_macos_wrapper() {
    echo "[错误] 无法创建 macOS 开发应用包装：$1" >&2
    exit 1
}

prepare_macos_dev_wrapper() {
    local profile_root="$1"
    local app_root="$profile_root/.sakura-dev/Sakura Runtime v2.app"
    local contents_root="$app_root/Contents"
    local executable_root="$contents_root/MacOS"
    local resources_root="$contents_root/Resources"
    local info_plist="$contents_root/Info.plist"
    local wrapper_executable="$executable_root/sakura"
    local icon_source="$PROJECT_ROOT/desktop/src-tauri/icons/icon.icns"
    local wrapper_icon="$resources_root/Sakura.icns"

    MACOS_WRAPPER_PLIST_TEMP="$contents_root/.Info.plist.$$.tmp"
    MACOS_WRAPPER_EXECUTABLE_TEMP="$executable_root/.sakura.$$.tmp"
    MACOS_WRAPPER_ICON_TEMP="$resources_root/.Sakura.icns.$$.tmp"
    trap cleanup_macos_wrapper_temporaries EXIT
    trap 'exit 129' HUP
    trap 'exit 130' INT
    trap 'exit 143' TERM

    if ! mkdir -p "$executable_root" "$resources_root"; then
        fail_macos_wrapper "无法建立 $app_root"
    fi
    if [ -d "$info_plist" ]; then
        fail_macos_wrapper "$info_plist 被目录占用"
    fi
    if [ -d "$wrapper_executable" ]; then
        fail_macos_wrapper "$wrapper_executable 被目录占用"
    fi
    if [ ! -f "$icon_source" ]; then
        fail_macos_wrapper "缺少开发应用图标 $icon_source"
    fi
    if [ -d "$wrapper_icon" ]; then
        fail_macos_wrapper "$wrapper_icon 被目录占用"
    fi
    if ! /bin/rm -f -- "$MACOS_WRAPPER_PLIST_TEMP" "$MACOS_WRAPPER_EXECUTABLE_TEMP" "$MACOS_WRAPPER_ICON_TEMP"; then
        fail_macos_wrapper "无法清理本次启动的临时文件"
    fi

    if ! /bin/cp "$icon_source" "$MACOS_WRAPPER_ICON_TEMP"; then
        fail_macos_wrapper "无法复制开发应用图标"
    fi
    if ! mv -f "$MACOS_WRAPPER_ICON_TEMP" "$wrapper_icon"; then
        fail_macos_wrapper "无法原子更新开发应用图标"
    fi
    MACOS_WRAPPER_ICON_TEMP=""

    if ! /bin/cat >"$MACOS_WRAPPER_PLIST_TEMP" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "https://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleDisplayName</key>
    <string>Sakura Runtime v2</string>
    <key>CFBundleExecutable</key>
    <string>sakura</string>
    <key>CFBundleIdentifier</key>
    <string>com.rvosy.sakura.runtimev2.shell</string>
    <key>CFBundleIconFile</key>
    <string>Sakura.icns</string>
    <key>CFBundleName</key>
    <string>Sakura Runtime v2</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
</dict>
</plist>
PLIST
    then
        fail_macos_wrapper "无法写入临时 Info.plist"
    fi
    if ! mv -f "$MACOS_WRAPPER_PLIST_TEMP" "$info_plist"; then
        fail_macos_wrapper "无法原子更新 Info.plist"
    fi
    MACOS_WRAPPER_PLIST_TEMP=""

    if ! ln -s "../../../../sakura" "$MACOS_WRAPPER_EXECUTABLE_TEMP"; then
        fail_macos_wrapper "无法建立临时 Mach-O 入口链接"
    fi
    if ! mv -f "$MACOS_WRAPPER_EXECUTABLE_TEMP" "$wrapper_executable"; then
        fail_macos_wrapper "无法原子更新 Mach-O 入口链接"
    fi
    MACOS_WRAPPER_EXECUTABLE_TEMP=""
    MACOS_WRAPPER_EXECUTABLE="$wrapper_executable"
}

# ============================================================
# 增量构建并启动开发版 Tauri Shell。release 属于 packaged 模式，必须由正式发行布局启动。
# ============================================================
cd "$PROJECT_ROOT"
if ! cargo build --manifest-path "$PROJECT_ROOT/desktop/src-tauri/Cargo.toml" --locked; then
    echo "[错误] Sakura Runtime v2 开发版编译失败。" >&2
    exit 1
fi
if ! SYSTEM_NAME="$(uname -s)"; then
    echo "[错误] 无法识别当前系统，Sakura Runtime v2 未启动。" >&2
    exit 1
fi
PROFILE_ROOT="$PROJECT_ROOT/desktop/src-tauri/target/debug"
TAURI_SHELL="$PROFILE_ROOT/sakura"
if [ -x "$TAURI_SHELL" ]; then
    if [ "$SYSTEM_NAME" = "Darwin" ]; then
        prepare_macos_dev_wrapper "$PROFILE_ROOT"
        exec "$MACOS_WRAPPER_EXECUTABLE" "$@"
    fi
    exec "$TAURI_SHELL" "$@"
fi

echo "[错误] 编译完成后仍未找到开发版 Tauri Shell。" >&2
exit 1
