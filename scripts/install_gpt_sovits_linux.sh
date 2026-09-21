#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$SCRIPT_DIR/../plugins/builtin/sakura_gpt_sovits/install_gpt_sovits_linux.sh" "$@"
