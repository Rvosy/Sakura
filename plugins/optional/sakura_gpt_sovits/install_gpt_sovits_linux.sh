#!/bin/bash
set -euo pipefail

BIND_ROOT="${1:-${SAKURA_TTS_INSTALL_DIR:-}}"
if [ -z "$BIND_ROOT" ]; then
    echo "usage: bash install_gpt_sovits_linux.sh <install-root>"
    exit 2
fi

if [ "$(uname -s)" != "Linux" ]; then
    echo "GPT-SoVITS Linux installer can only run on Linux."
    exit 2
fi

ARCH="$(uname -m)"
case "$ARCH" in
x86_64 | aarch64) ;;
*)
    echo "Unsupported Linux architecture: $ARCH"
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
require_command unzip
require_command tar
require_command ffmpeg

BIND_PARENT="$(dirname "$BIND_ROOT")"
mkdir -p "$BIND_PARENT"
BIND_ROOT="$(cd "$BIND_PARENT" && pwd)/$(basename "$BIND_ROOT")"
ENV_DIR="${SAKURA_SOVITS_ENV:-$BIND_ROOT/sovits}"
DOWNLOADS_DIR="${SAKURA_TTS_DOWNLOADS_DIR:-$BIND_ROOT/downloads}"
GPT_DIR="$BIND_ROOT/GPT-SoVITS"
ENV_PYTHON="$ENV_DIR/bin/python"
GPT_REPO="${GPT_SOVITS_REPO:-https://github.com/RVC-Boss/GPT-SoVITS.git}"
GPT_REF="${GPT_SOVITS_REF:-08d627c3338173c3229286d8787060d6559fe0f8}"
MODEL_SOURCE="${GPT_SOVITS_MODEL_SOURCE:-HF}"
INSTALL_DEVICE="${GPT_SOVITS_INSTALL_DEVICE:-${GPT_SOVITS_DEVICE:-CU128}}"
INFER_DEVICE="${GPT_SOVITS_INFER_DEVICE:-cuda:0}"
CONFIG_PATH="$GPT_DIR/GPT_SoVITS/configs/tts_infer_sakura_linux.yaml"

unset PIP_INDEX_URL PIP_EXTRA_INDEX_URL UV_DEFAULT_INDEX CONDA_CHANNEL_ALIAS CONDARC || true

find_conda() {
    if [ -n "${CONDA_EXE:-}" ] && [ -x "$CONDA_EXE" ]; then
        echo "$CONDA_EXE"
        return 0
    fi
    if command -v conda >/dev/null 2>&1; then
        command -v conda
        return 0
    fi
    local candidate
    for candidate in \
        "$HOME/anaconda3/bin/conda" \
        "$HOME/miniconda3/bin/conda" \
        "$HOME/miniforge3/bin/conda" \
        /opt/conda/bin/conda
    do
        if [ -x "$candidate" ]; then
            echo "$candidate"
            return 0
        fi
    done
    return 1
}

download_file() {
    local url="$1"
    local dest="$2"
    mkdir -p "$(dirname "$dest")"
    curl -fL --retry 8 --retry-delay 3 -C - -o "$dest.part" "$url"
    mv -f "$dest.part" "$dest"
}

torch_index() {
    case "$INSTALL_DEVICE" in
    CU128) echo "https://download.pytorch.org/whl/cu128" ;;
    CU126) echo "https://download.pytorch.org/whl/cu126" ;;
    CPU) echo "https://download.pytorch.org/whl/cpu" ;;
    *)
        echo "Unsupported GPT_SOVITS_INSTALL_DEVICE: $INSTALL_DEVICE (use CU128, CU126, or CPU)"
        exit 2
        ;;
    esac
}

pretrained_base() {
    case "$MODEL_SOURCE" in
    ModelScope)
        echo "https://www.modelscope.cn/models/XXXXRT/GPT-SoVITS-Pretrained/resolve/master"
        ;;
    HF | HuggingFace)
        echo "https://huggingface.co/XXXXRT/GPT-SoVITS-Pretrained/resolve/main"
        ;;
    HF-Mirror)
        echo "https://hf-mirror.com/XXXXRT/GPT-SoVITS-Pretrained/resolve/main"
        ;;
    *)
        echo "Unsupported GPT_SOVITS_MODEL_SOURCE: $MODEL_SOURCE (use HF, HF-Mirror, or ModelScope)"
        exit 2
        ;;
    esac
}

CONDA_BIN="$(find_conda)" || {
    echo "conda not found. Install Anaconda/Miniconda or set CONDA_EXE."
    exit 2
}

mkdir -p "$BIND_ROOT" "$DOWNLOADS_DIR"

progress prepare 5
if [ -x "$ENV_PYTHON" ]; then
    ENV_VERSION="$("$ENV_PYTHON" -c 'import sys; print("%s.%s" % sys.version_info[:2])')"
    if [ "$ENV_VERSION" != "3.10" ]; then
        echo "Existing env at $ENV_DIR is Python $ENV_VERSION, expected 3.10."
        exit 1
    fi
else
    progress install 20
    echo "Creating conda env at $ENV_DIR with python=3.10 only."
    "$CONDA_BIN" create -y -p "$ENV_DIR" python=3.10 --override-channels -c conda-forge
fi

if [ ! -x "$ENV_PYTHON" ]; then
    echo "Failed to create conda env python: $ENV_PYTHON"
    exit 1
fi
if [ "$ENV_DIR" != "$BIND_ROOT/sovits" ]; then
    ln -sfn "$ENV_DIR" "$BIND_ROOT/sovits"
fi

progress download 40
if [ ! -d "$GPT_DIR/.git" ]; then
    rm -rf "$GPT_DIR"
    git init "$GPT_DIR"
    git -C "$GPT_DIR" remote add origin "$GPT_REPO"
fi
git -C "$GPT_DIR" fetch --depth 1 origin "$GPT_REF"
git -C "$GPT_DIR" checkout --detach FETCH_HEAD
if [ ! -f "$GPT_DIR/install.sh" ]; then
    echo "GPT-SoVITS install.sh not found: $GPT_DIR"
    exit 1
fi

progress install 55
cd "$GPT_DIR"
"$ENV_PYTHON" -m pip install --upgrade pip
"$ENV_PYTHON" -m pip install torch torchaudio torchcodec --index-url "$(torch_index)"
"$ENV_PYTHON" -m pip install -r extra-req.txt --no-deps
"$ENV_PYTHON" -m pip install -r requirements.txt
# requirements.txt 会从 PyPI 装到默认 CUDA 轮子，必须再用同一套 PyTorch 索引钉回。
"$ENV_PYTHON" -m pip install --force-reinstall --no-deps torchaudio --index-url "$(torch_index)"

progress download 75
BASE="$(pretrained_base)"
if [ ! -d "$GPT_DIR/GPT_SoVITS/pretrained_models/sv" ]; then
    download_file "$BASE/pretrained_models.zip" "$DOWNLOADS_DIR/pretrained_models.zip"
    unzip -q -o "$DOWNLOADS_DIR/pretrained_models.zip" -d "$GPT_DIR/GPT_SoVITS"
fi
if [ ! -d "$GPT_DIR/GPT_SoVITS/text/G2PWModel" ]; then
    download_file "$BASE/G2PWModel.zip" "$DOWNLOADS_DIR/G2PWModel.zip"
    unzip -q -o "$DOWNLOADS_DIR/G2PWModel.zip" -d "$GPT_DIR/GPT_SoVITS/text"
fi

PREFIX="$("$ENV_PYTHON" -c "import sys; print(sys.prefix)")"
JTALK="$("$ENV_PYTHON" -c "import os, pyopenjtalk; print(os.path.dirname(pyopenjtalk.__file__))")"
if [ ! -d "$PREFIX/nltk_data" ] && [ ! -d "$PREFIX/lib/nltk_data" ]; then
    download_file "$BASE/nltk_data.zip" "$DOWNLOADS_DIR/nltk_data.zip"
    unzip -q -o "$DOWNLOADS_DIR/nltk_data.zip" -d "$PREFIX"
fi
if [ ! -d "$JTALK/open_jtalk_dic_utf_8-1.11" ]; then
    download_file "$BASE/open_jtalk_dic_utf_8-1.11.tar.gz" "$DOWNLOADS_DIR/open_jtalk_dic_utf_8-1.11.tar.gz"
    tar -xzf "$DOWNLOADS_DIR/open_jtalk_dic_utf_8-1.11.tar.gz" -C "$JTALK"
fi

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
device = os.environ.get("SAKURA_TTS_INFER_DEVICE", "cuda:0").strip().lower() or "cuda:0"
custom["device"] = device
custom["is_half"] = device.startswith("cuda")
data["custom"] = custom
config_path.parent.mkdir(parents=True, exist_ok=True)
config_path.write_text(
    yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
    encoding="utf-8",
)
print(config_path)
PY


progress cleanup 100
echo "GPT-SoVITS Linux installer completed."
echo "conda env: $ENV_DIR"
echo "python:    $ENV_PYTHON"
echo "work dir:  $GPT_DIR"
echo "config:    $CONFIG_PATH"
