#!/bin/bash
set -euo pipefail

INSTALL_ROOT="${1:-${SAKURA_TTS_INSTALL_DIR:-}}"
if [ -z "$INSTALL_ROOT" ]; then
    echo "usage: bash install_gpt_sovits_macos.sh <install-root>"
    exit 2
fi

if [ "$(uname -s)" != "Darwin" ]; then
    echo "GPT-SoVITS macOS installer can only run on macOS."
    exit 2
fi

ARCH="$(uname -m)"
case "$ARCH" in
arm64 | x86_64) ;;
*)
    echo "Unsupported macOS architecture: $ARCH"
    exit 2
    ;;
esac

progress() {
    echo "::sakura-progress status=$1 progress=$2"
}

require_command() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "Missing required command: $1"
        exit 2
    fi
}

require_command curl
require_command git
require_command shasum

INSTALL_PARENT="$(dirname "$INSTALL_ROOT")"
mkdir -p "$INSTALL_PARENT"
INSTALL_ROOT="$(cd "$INSTALL_PARENT" && pwd)/$(basename "$INSTALL_ROOT")"
DOWNLOADS_DIR="${SAKURA_TTS_DOWNLOADS_DIR:-$INSTALL_ROOT/downloads}"
MINIFORGE_DIR="$INSTALL_ROOT/miniforge3"
ENV_NAME="gpt-sovits310"
ENV_DIR="$MINIFORGE_DIR/envs/$ENV_NAME"
ENV_PYTHON="$ENV_DIR/bin/python"
GPT_DIR="$INSTALL_ROOT/GPT-SoVITS"
GPT_REPO="${GPT_SOVITS_REPO:-https://github.com/RVC-Boss/GPT-SoVITS.git}"
GPT_REF="${GPT_SOVITS_REF:-08d627c3338173c3229286d8787060d6559fe0f8}"
MODEL_SOURCE="${GPT_SOVITS_MODEL_SOURCE:-ModelScope}"
INSTALL_DEVICE="${GPT_SOVITS_INSTALL_DEVICE:-${GPT_SOVITS_DEVICE:-MPS}}"
INFER_DEVICE="${GPT_SOVITS_INFER_DEVICE:-cpu}"
CONFIG_PATH="$GPT_DIR/GPT_SoVITS/configs/tts_infer_sakura_macos.yaml"
MINIFORGE_VERSION="26.3.2-3"
MINIFORGE_FILENAME="Miniforge3-$MINIFORGE_VERSION-MacOSX-$ARCH.sh"
MINIFORGE_URL="https://github.com/conda-forge/miniforge/releases/download/$MINIFORGE_VERSION/$MINIFORGE_FILENAME"
case "$ARCH" in
arm64)
    MINIFORGE_SIZE=53402258
    ;;
x86_64)
    MINIFORGE_SIZE=60470361
    ;;
esac

verify_installer() {
    local file="$1"
    [ -f "$file" ] && [ ! -L "$file" ] || return 1
    [ "$(stat -f %z "$file")" = "$MINIFORGE_SIZE" ] || return 1
    # Read only the script header; the bundled installer validates its payload.
    [ "$(head -c 10 "$file")" = '#!/bin/sh' ] || return 1
}

conda_usable() {
    [ -x "$MINIFORGE_DIR/bin/conda" ] || return 1
    [ -f "$MINIFORGE_DIR/etc/profile.d/conda.sh" ] || return 1
    "$MINIFORGE_DIR/bin/conda" --version >/dev/null 2>&1 || return 1
}

mkdir -p "$INSTALL_ROOT" "$DOWNLOADS_DIR"

progress prepare 5
if [ -d "$MINIFORGE_DIR" ] && ! conda_usable; then
    echo "Existing Miniforge runtime is unusable, reinstalling: $MINIFORGE_DIR"
    rm -rf "$MINIFORGE_DIR"
fi

if ! conda_usable; then
    INSTALLER="$DOWNLOADS_DIR/$MINIFORGE_FILENAME"
    if [ -f "$INSTALLER" ] && ! verify_installer "$INSTALLER"; then
        rm -f "$INSTALLER"
    fi
    if [ ! -f "$INSTALLER" ]; then
        progress download 10
        curl -fL -o "$INSTALLER.part" "$MINIFORGE_URL"
        verify_installer "$INSTALLER.part"
        mv -f "$INSTALLER.part" "$INSTALLER"
    fi
    verify_installer "$INSTALLER"
    progress install 20
    bash "$INSTALLER" -b -p "$MINIFORGE_DIR"
fi

if ! conda_usable; then
    echo "Miniforge runtime is still unusable after installation: $MINIFORGE_DIR"
    exit 1
fi

# shellcheck source=/dev/null
source "$MINIFORGE_DIR/etc/profile.d/conda.sh"

if [ ! -x "$ENV_PYTHON" ]; then
    progress install 30
    conda create -y -p "$ENV_DIR" python=3.10
fi

progress install 38
conda activate "$ENV_DIR"
conda install -y -c conda-forge wget

if [ ! -d "$GPT_DIR/.git" ]; then
    progress download 45
    rm -rf "$GPT_DIR"
    git clone "$GPT_REPO" "$GPT_DIR"
fi

progress install 55
git -C "$GPT_DIR" fetch --tags origin
git -C "$GPT_DIR" checkout "$GPT_REF"

if [ ! -f "$GPT_DIR/install.sh" ]; then
    echo "GPT-SoVITS install.sh not found: $GPT_DIR"
    exit 1
fi

progress install 65
cd "$GPT_DIR"
WORKFLOW=false bash install.sh --device "$INSTALL_DEVICE" --source "$MODEL_SOURCE"

progress configure 92
SAKURA_TTS_CONFIG_PATH="$CONFIG_PATH" SAKURA_TTS_INFER_DEVICE="$INFER_DEVICE" "$ENV_PYTHON" - <<'PY'
from __future__ import annotations

import os
from pathlib import Path

import yaml

config_path = Path(os.environ["SAKURA_TTS_CONFIG_PATH"])
source_path = config_path.with_name("tts_infer.yaml")
data = yaml.safe_load(source_path.read_text(encoding="utf-8")) or {}
preferred = dict(data.get("v2ProPlus") or data.get("v2") or {})
custom = dict(data.get("custom") or {})
custom.update(preferred)
custom["device"] = os.environ.get("SAKURA_TTS_INFER_DEVICE", "cpu").strip().lower() or "cpu"
custom["is_half"] = False
data["custom"] = custom
config_path.write_text(
    yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
    encoding="utf-8",
)
PY

progress cleanup 100
echo "GPT-SoVITS macOS installer completed."
