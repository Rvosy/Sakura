#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SAKURA_ROOT="${SAKURA_ROOT:-$REPO_ROOT}"
ENV_DIR="${SAKURA_SOVITS_ENV:-$SAKURA_ROOT/envs/sovits}"
TTS_ROOT="${SAKURA_TTS_ROOT:-$SAKURA_ROOT/tts}"
BIND_ROOT="${1:-${SAKURA_TTS_INSTALL_DIR:-$TTS_ROOT/gpt_sovits_linux}}"
BIND_PARENT="$(dirname "$BIND_ROOT")"
mkdir -p "$BIND_PARENT"
BIND_ROOT="$(cd "$BIND_PARENT" && pwd)/$(basename "$BIND_ROOT")"
ENV_PYTHON="$ENV_DIR/bin/python"
GPT_DIR="$BIND_ROOT/GPT-SoVITS"
CONFIG_PATH="$GPT_DIR/GPT_SoVITS/configs/tts_infer_sakura_linux.yaml"
USER_ROOT="${SAKURA_USER_ROOT:-${XDG_DATA_HOME:-$HOME/.local/share}/Sakura Development}"

SAKURA_SOVITS_ENV="$ENV_DIR" \
SAKURA_TTS_DOWNLOADS_DIR="${SAKURA_TTS_DOWNLOADS_DIR:-$TTS_ROOT/_dl}" \
bash "$SCRIPT_DIR/../plugins/optional/sakura_gpt_sovits/install_gpt_sovits_linux.sh" "$BIND_ROOT"

SAKURA_USER_ROOT="$USER_ROOT" SAKURA_TTS_ROOT="$TTS_ROOT" \
SAKURA_GPT_DIR="$GPT_DIR" SAKURA_ENV_PYTHON="$ENV_PYTHON" \
SAKURA_TTS_CONFIG_PATH="$CONFIG_PATH" "$ENV_PYTHON" - <<'PY'
from __future__ import annotations

import json
import os
from pathlib import Path

user_root = Path(os.environ["SAKURA_USER_ROOT"])
tts_root = Path(os.environ["SAKURA_TTS_ROOT"]).resolve()
storage = {
    "schemaVersion": 1,
    "ttsRoot": str(tts_root),
}
storage_path = user_root / "config" / "storage.json"
storage_path.parent.mkdir(parents=True, exist_ok=True)
storage_path.write_text(json.dumps(storage, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
plugin_dir = user_root / "data" / "plugins" / "sakura.tts.gpt-sovits"
plugin_dir.mkdir(parents=True, exist_ok=True)
config_path = plugin_dir / "config.json"
payload = {}
if config_path.is_file():
    try:
        loaded = json.loads(config_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            payload = loaded
    except json.JSONDecodeError:
        payload = {}
payload.update(
    {
        "endpointMode": "managed",
        "customBaseUrl": "",
        "workDir": os.environ["SAKURA_GPT_DIR"],
        "pythonPath": os.environ["SAKURA_ENV_PYTHON"],
        "ttsConfigPath": os.environ["SAKURA_TTS_CONFIG_PATH"],
    }
)
config_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(storage_path)
print(config_path)
PY
