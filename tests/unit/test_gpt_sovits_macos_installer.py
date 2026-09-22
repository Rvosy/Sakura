from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


pytestmark = pytest.mark.skipif(os.name == "nt", reason="macOS installer requires a Unix Bash environment")


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = [
    ROOT / "plugins/optional/sakura_gpt_sovits/install_gpt_sovits_macos.sh",
    ROOT / "scripts/install_gpt_sovits_macos.sh",
]
MINIFORGE_URL = (
    "https://github.com/conda-forge/miniforge/releases/download/26.3.2-3/"
    "Miniforge3-26.3.2-3-MacOSX-arm64.sh"
)
GPT_REPO = "https://github.com/RVC-Boss/GPT-SoVITS.git"
GPT_REF = "08d627c3338173c3229286d8787060d6559fe0f8"

# The pinned upstream installer uses this progress cleanup after wget succeeds.
UPSTREAM_PROGRESS_SCRIPT = '''#!/bin/bash
set -eE
run_wget_quiet() {
    if wget --tries=25 --wait=5 --read-timeout=40 -q --show-progress "$@" 2>&1; then
        if [ "$WORKFLOW" = "false" ]; then
            tput cuu1 && tput el
        fi
    else
        echo "Wget failed"
        exit 1
    fi
}
run_wget_quiet https://modelscope.example/model.zip
if [ "$WORKFLOW" = "false" ]; then
    fake_torch
fi
fake_upstream "$@"
'''

FAKE_COMMAND = r'''
import json
import os
import sys
from pathlib import Path

name = Path(sys.argv[0]).name
args = sys.argv[1:]
if name == "uname":
    print("Darwin" if args == ["-s"] else "arm64")
    raise SystemExit(0)
if name == "stat":
    print(Path(args[-1]).stat().st_size)
    raise SystemExit(0)

log = Path(os.environ["FAKE_LOG"])
previous = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
with log.open("a") as stream:
    stream.write(json.dumps({"name": name, "args": args}) + "\n")
scenario = json.loads(os.environ["FAKE_SCENARIO"])
if name == "wget":
    if scenario.get("wget"):
        print("wget original transport error", file=sys.stderr)
        raise SystemExit(scenario["wget"])
elif name == "tput":
    if "TERM" not in os.environ:
        print("tput: No value for $TERM and no -T specified", file=sys.stderr)
        raise SystemExit(2)
elif name == "curl":
    index = sum(row["name"] == "curl" for row in previous)
    result = scenario.get("curl", [0])[index]
    output = Path(args[args.index("-o") + 1])
    if isinstance(result, int) and result:
        output.write_bytes(b"partial transfer")
        print(f"curl original error {result}", file=sys.stderr)
        raise SystemExit(result)
    installer = b"""#!/bin/sh
while [ "$#" -gt 0 ]; do
    if [ "$1" = "-p" ]; then prefix="$2"; shift; fi
    shift
done
mkdir -p "$prefix"
cp -R "$FAKE_RUNTIME/." "$prefix/"
exit 0
"""
    if result == "bad_header":
        installer = b"not shell\n" + installer
    with output.open("wb") as stream:
        stream.write(installer)
        stream.truncate(512 if result == "bad_size" else 53402258)
elif name == "git":
    git_args = args[2:] if args[:2] == ["-c", "http.userAgent=Sakura"] else args
    if git_args[0] == "init":
        (Path(git_args[1]) / ".git").mkdir(parents=True)
    elif git_args[2] == "fetch":
        index = sum(row["name"] == "git" and "fetch" in row["args"] for row in previous)
        result = scenario.get("fetch", [0])[index]
        if result:
            print(f"git original fetch error {index + 1}", file=sys.stderr)
            raise SystemExit(result)
    elif git_args[2] == "checkout":
        if scenario.get("checkout"):
            print("git original checkout error", file=sys.stderr)
            raise SystemExit(scenario["checkout"])
        (Path(git_args[1]) / "install.sh").write_text(
            scenario.get("upstream", "#!/bin/bash\nfake_upstream \"$@\"\n")
        )
'''


@pytest.fixture(params=SCRIPTS, ids=["plugin", "scripts"])
def installer(tmp_path, request):
    commands = tmp_path / "commands"
    commands.mkdir()
    runtime = tmp_path / "runtime"
    for name in ("curl", "git", "uname", "stat", "wget", "tput", "fake_torch", "fake_upstream"):
        path = commands / name
        path.write_text(f"#!{sys.executable}\n{FAKE_COMMAND}")
        path.chmod(0o755)
    for relative in ("bin/conda", "envs/gpt-sovits310/bin/python"):
        path = runtime / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"#!{sys.executable}\n{FAKE_COMMAND}")
        path.chmod(0o755)
    conda_setup = runtime / "etc/profile.d/conda.sh"
    conda_setup.parent.mkdir(parents=True)
    conda_setup.write_text('conda() { "$FAKE_RUNTIME/bin/conda" "$@"; }\n')
    install_root = tmp_path / "installation"
    log = tmp_path / "calls.jsonl"

    def run(scenario=None, *, preinstalled=False, overrides=None):
        if preinstalled:
            shutil.copytree(runtime, install_root / "miniforge3")
        environment = {
            key: value for key, value in os.environ.items()
            if key != "TERM" and not key.startswith(("GPT_SOVITS_", "SAKURA_TTS_"))
        }
        environment.update({
            "PATH": str(commands) + os.pathsep + os.environ["PATH"],
            "FAKE_RUNTIME": str(runtime),
            "FAKE_LOG": str(log),
            "FAKE_SCENARIO": json.dumps(scenario or {}),
            **(overrides or {}),
        })
        result = subprocess.run(
            ["bash", str(request.param), str(install_root)],
            capture_output=True, text=True, env=environment, timeout=20,
        )
        calls = [json.loads(line) for line in log.read_text().splitlines()]
        return result, calls

    return run


def test_download_transport_fallback_keeps_pinned_versions_and_runs_installer(installer):
    result, calls = installer({"curl": [22, 18, 0], "fetch": [128, 0]})
    assert result.returncode == 0, result.stderr
    downloads = [row["args"] for row in calls if row["name"] == "curl"]
    assert [args[-1] for args in downloads] == [
        "https://gitproxy.mrhjx.cn/" + MINIFORGE_URL,
        "https://ghproxy.vip/" + MINIFORGE_URL,
        MINIFORGE_URL,
    ]
    assert all(args[args.index("-A") + 1] == "Sakura" for args in downloads)
    fetches = [row["args"] for row in calls if row["name"] == "git" and "fetch" in row["args"]]
    assert [args[-2:] for args in fetches] == [
        ["https://gitproxy.mrhjx.cn/" + GPT_REPO, GPT_REF], [GPT_REPO, GPT_REF],
    ]
    assert all(args[:2] == ["-c", "http.userAgent=Sakura"] for args in fetches)
    upstream = next(row for row in calls if row["name"] == "fake_upstream")
    assert upstream["args"] == ["--device", "MPS", "--source", "ModelScope"]
    assert "curl original error 22" in result.stderr
    assert "curl original error 18" in result.stderr
    assert "git original fetch error 1" in result.stderr


@pytest.mark.parametrize("result_kind", ["bad_header", "bad_size", 23])
def test_invalid_package_or_local_write_failure_does_not_switch_source(installer, result_kind):
    result, calls = installer({"curl": [result_kind]})
    assert result.returncode != 0
    assert len([row for row in calls if row["name"] == "curl"]) == 1
    assert not any(row["name"] == "git" for row in calls)
    if result_kind == 23:
        assert "curl original error 23" in result.stderr
    else:
        assert "unexpected size or header" in result.stderr


def test_explicit_miniforge_url_is_used_without_public_mirrors(installer):
    private_url = "https://private.example/Miniforge.sh?token=fixture"
    result, calls = installer({"curl": [22]}, overrides={"GPT_SOVITS_MINIFORGE_URL": private_url})
    assert result.returncode == 22
    assert [row["args"][-1] for row in calls if row["name"] == "curl"] == [private_url]


def test_explicit_repository_is_used_without_public_mirrors(installer):
    private_repo = "ssh://git@private.example/GPT-SoVITS.git"
    result, calls = installer(
        {"fetch": [128]}, preinstalled=True, overrides={"GPT_SOVITS_REPO": private_repo},
    )
    assert result.returncode == 128
    fetches = [row["args"] for row in calls if row["name"] == "git" and "fetch" in row["args"]]
    assert [args[-2:] for args in fetches] == [[private_repo, GPT_REF]]
    assert "git original fetch error 1" in result.stderr


def test_checkout_failure_keeps_error_without_fetching_another_source(installer):
    result, calls = installer({"checkout": 128}, preinstalled=True)
    assert result.returncode == 128
    assert "git original checkout error" in result.stderr
    assert len([row for row in calls if row["name"] == "git" and "fetch" in row["args"]]) == 1
    assert not any(row["name"] == "fake_upstream" for row in calls)


def test_desktop_progress_without_terminal_keeps_upstream_torch_install(installer):
    result, calls = installer({"upstream": UPSTREAM_PROGRESS_SCRIPT}, preinstalled=True)
    assert result.returncode == 0, result.stderr
    assert any(row["name"] == "wget" for row in calls)
    assert any(row["name"] == "fake_torch" for row in calls)
    assert any(row["name"] == "fake_upstream" for row in calls)
    assert not any(row["name"] == "tput" for row in calls)


def test_desktop_progress_does_not_hide_upstream_download_failure(installer):
    result, calls = installer({"upstream": UPSTREAM_PROGRESS_SCRIPT, "wget": 7}, preinstalled=True)
    assert result.returncode == 1
    assert "wget original transport error" in result.stdout
    assert not any(row["name"] in {"fake_torch", "fake_upstream", "python"} for row in calls)
