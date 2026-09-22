from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.skipif(sys.platform == "win32", reason="Linux installer requires Bash")
@pytest.mark.parametrize("source_entry", [False, True])
def test_linux_installer_keeps_plugin_staging_separate_from_source_configuration(
    tmp_path: Path, source_entry: bool
) -> None:
    repo = tmp_path / "source"
    user_root = tmp_path / "user"
    fixture_bin = tmp_path / "bin"
    fixture_bin.mkdir()
    project = Path(__file__).resolve().parents[2]
    scripts = (
        "scripts/install_gpt_sovits_linux.sh",
        "plugins/optional/sakura_gpt_sovits/install_gpt_sovits_linux.sh",
    )
    for relative in scripts:
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(project / relative, target)

    install_root = repo / "tts" / "gpt_sovits_linux" if source_entry else tmp_path / "staging"
    env_dir = repo / "envs" / "sovits" if source_entry else install_root / "sovits"
    (env_dir / "bin").mkdir(parents=True)
    python = env_dir / "bin" / "python"
    python.write_text(
        '#!/bin/bash\n'
        'if [ "$1" = "-m" ]; then exit 0; fi\n'
        'if [ "$1" = "-c" ]; then\n'
        '  case "$2" in\n'
        '    *sys.version_info*) echo 3.10 ;;\n'
        '    *sys.prefix*) echo "$TEST_ENV_DIR" ;;\n'
        '    *pyopenjtalk*) echo "$TEST_ENV_DIR/jtalk" ;;\n'
        '  esac\n'
        '  exit 0\n'
        'fi\n'
        'exec "$TEST_PYTHON" "$@"\n',
        encoding="utf-8",
    )
    python.chmod(0o755)
    for name in ("uname", "git", "conda", "curl", "unzip", "tar", "ffmpeg"):
        command = fixture_bin / name
        command.write_text(
            '#!/bin/bash\n' + ('if [ "$1" = "-m" ]; then echo x86_64; else echo Linux; fi\n' if name == "uname" else "exit 0\n"),
            encoding="utf-8",
        )
        command.chmod(0o755)
    gpt_dir = install_root / "GPT-SoVITS"
    for directory in (
        gpt_dir / ".git", gpt_dir / "GPT_SoVITS/pretrained_models/sv",
        gpt_dir / "GPT_SoVITS/text/G2PWModel", gpt_dir / "GPT_SoVITS/configs",
        env_dir / "nltk_data", env_dir / "jtalk/open_jtalk_dic_utf_8-1.11",
    ):
        directory.mkdir(parents=True)
    (gpt_dir / "install.sh").write_text("", encoding="utf-8")
    (gpt_dir / "GPT_SoVITS/configs/tts_infer.yaml").write_text("v2: {}\n", encoding="utf-8")
    config_path = user_root / "data/plugins/sakura.tts.gpt-sovits/config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text('{"timeoutSeconds": 42}\n', encoding="utf-8")
    original = config_path.read_text(encoding="utf-8")
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("SAKURA_", "GPT_SOVITS_", "CONDA"))
    }
    env.update({
        "HOME": str(tmp_path / "home"),
        "PATH": str(fixture_bin) + os.pathsep + env.get("PATH", ""),
        "SAKURA_USER_ROOT": str(user_root),
        "TEST_PYTHON": sys.executable,
        "TEST_ENV_DIR": str(env_dir),
    })
    command = ["bash", str(repo / scripts[0 if source_entry else 1])]
    if not source_entry:
        command.append(str(install_root))
    result = subprocess.run(command, env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (gpt_dir / "GPT_SoVITS/configs/tts_infer_sakura_linux.yaml").is_file()
    assert (install_root / "sovits/bin/python").is_file()
    if source_entry:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        assert config["timeoutSeconds"] == 42
        assert config["workDir"] == str(gpt_dir)
        assert config["pythonPath"] == str(python)
        assert json.loads((user_root / "config/storage.json").read_text(encoding="utf-8"))["ttsRoot"] == str(repo / "tts")
    else:
        assert config_path.read_text(encoding="utf-8") == original
        assert not (user_root / "config/storage.json").exists()
        assert not (repo / "envs").exists()
