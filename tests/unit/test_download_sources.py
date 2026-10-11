from __future__ import annotations

import io
import json
from pathlib import Path
from subprocess import CompletedProcess
from urllib.parse import urlsplit

import pytest

from app.plugin_sdk.sakura_downloads import DEFAULT_PYPI_INDEX, uv_download_environment
from app.plugins.dependencies import PluginDependencyError, PluginDependencyRoots


def test_default_index_and_proxy_reach_plugin_install_process(tmp_path, monkeypatch):
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    (plugin / "requirements.txt").write_text("fixture==1.0\n", encoding="utf-8")
    # Isolate explicit configuration from the developer's environment.
    monkeypatch.setattr("app.plugins.dependencies.uv_download_environment", lambda path, **kwargs: uv_download_environment(path, {}, **kwargs))
    monkeypatch.setattr("urllib.request.getproxies", lambda: {"https": "http://127.0.0.1:7890"})
    captured = []

    def run(command, **kwargs):
        from subprocess import CompletedProcess
        captured.append(kwargs["env"])
        return CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("app.plugins.dependencies.subprocess.run", run)
    root = PluginDependencyRoots(tmp_path / "user").install("example.plugin", plugin)
    assert root is not None
    assert len(captured) == 1
    assert captured[0]["UV_DEFAULT_INDEX"] == DEFAULT_PYPI_INDEX
    assert captured[0]["HTTPS_PROXY"] == "http://127.0.0.1:7890"
    assert (plugin / "requirements.txt").read_text() == "fixture==1.0\n"


@pytest.mark.parametrize("key", ["UV_DEFAULT_INDEX", "UV_INDEX_URL", "UV_INDEX", "UV_CONFIG_FILE"])
def test_explicit_uv_source_is_preserved(tmp_path, key):
    environment = {key: "custom", "PIP_INDEX_URL": "https://pip.example/simple"}
    assert uv_download_environment(tmp_path, environment) == environment


def test_pip_source_is_forwarded_to_uv_without_mutating_parent(tmp_path):
    original = {"PIP_INDEX_URL": "https://pypi.org/simple"}
    result = uv_download_environment(tmp_path, original)
    assert result["UV_DEFAULT_INDEX"] == original["PIP_INDEX_URL"]
    assert "UV_DEFAULT_INDEX" not in original


@pytest.mark.parametrize(("name", "content"), [
    ("uv.toml", '[[index]]\nurl = "https://private.example/simple"\ndefault = true\n'),
    ("pyproject.toml", '[tool.uv.pip]\nindex-url = "https://private.example/simple"\n'),
    ("requirements.txt", '--index-url https://private.example/simple\nfixture==1\n'),
    ("requirements.lock", '--no-index\n--find-links ./wheels\nfixture==1\n'),
])
def test_plugin_source_declarations_are_not_overridden(tmp_path, name, content):
    (tmp_path / name).write_text(content, encoding="utf-8")
    assert "UV_DEFAULT_INDEX" not in uv_download_environment(tmp_path, {})


def test_user_uv_configuration_is_preserved(tmp_path, monkeypatch):
    monkeypatch.setattr("app.plugin_sdk.sakura_downloads.sys.platform", "win32")
    user_config = tmp_path / "profile/uv/uv.toml"
    user_config.parent.mkdir(parents=True)
    user_config.write_text('[pip]\nno-index = true\n', encoding="utf-8")
    assert "UV_DEFAULT_INDEX" not in uv_download_environment(tmp_path / "plugin", {"APPDATA": str(tmp_path / "profile")})


ALIYUN = "https://mirrors.aliyun.com/pypi/simple"
HUAWEI = "https://repo.huaweicloud.com/repository/pypi/simple"
PYPI = "https://pypi.org/simple"


def missing_wheel(index: str, suffix: str = ".metadata") -> str:
    origin = urlsplit(index)
    return (
        "error: Failed to fetch\nCaused by: HTTP status client error (404 Not Found) for url "
        f"({origin.scheme}://{origin.netloc}/packages/fixture-1.0-py3-none-any.whl{suffix})"
    )


@pytest.fixture
def dependency_download(tmp_path, monkeypatch):
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    (plugin / "requirements.txt").write_text("fixture==1.0\n", encoding="utf-8")
    source_environment = {}
    monkeypatch.setattr(
        "app.plugins.dependencies.uv_download_environment",
        lambda path, **kwargs: uv_download_environment(path, source_environment, **kwargs),
    )
    monkeypatch.setattr("urllib.request.getproxies", lambda: {})
    roots = PluginDependencyRoots(tmp_path / "user")
    calls = []
    outcomes = []

    def run(command, **kwargs):
        target = Path(command[command.index("--target") + 1])
        calls.append((command, kwargs["env"]))
        outcome = outcomes.pop(0)
        if isinstance(outcome, OSError):
            raise outcome
        if not outcome:
            (target / "fixture.py").write_text("value = 1\n")
        return CompletedProcess(command, 2 if outcome else 0, "", outcome)

    monkeypatch.setattr("app.plugins.dependencies.subprocess.run", run)
    return roots, plugin, calls, outcomes, source_environment


@pytest.mark.parametrize("succeed_on,suffix", [(1, ".metadata"), (2, ".metadata"), (3, ".metadata"), (2, "")])
def test_default_mirror_recovers_missing_files_in_order(dependency_download, succeed_on, suffix):
    roots, plugin, calls, outcomes, _ = dependency_download
    indexes = [ALIYUN, ALIYUN, HUAWEI, PYPI]
    errors = [missing_wheel(index, suffix) for index in indexes[:succeed_on]]
    if not suffix:
        errors = [error.replace("404 Not Found", "404 File not found") for error in errors]
    outcomes.extend(errors + [""])
    root = roots.install("fixture", plugin)
    assert root is not None
    assert (root / "fixture.py").is_file()
    assert [env["UV_DEFAULT_INDEX"] for _, env in calls] == indexes[:succeed_on + 1]
    assert ["--refresh" in command for command, _ in calls] == [False] + [True] * succeed_on
    assert len({env["UV_CACHE_DIR"] for _, env in calls}) == 1
    assert (plugin / "requirements.txt").read_text() == "fixture==1.0\n"


@pytest.mark.parametrize("explicit", ["uv", "pip", "extra", "requirements", "config"])
def test_missing_file_does_not_override_explicit_sources(dependency_download, explicit):
    roots, plugin, calls, outcomes, environment = dependency_download
    if explicit == "uv":
        environment["UV_DEFAULT_INDEX"] = ALIYUN
    elif explicit == "pip":
        environment["PIP_INDEX_URL"] = ALIYUN
    elif explicit == "extra":
        environment["PIP_EXTRA_INDEX_URL"] = "https://private.example/simple"
    elif explicit == "requirements":
        (plugin / "requirements.txt").write_text(f"--index-url {ALIYUN}\nfixture==1.0\n")
    else:
        (plugin / "uv.toml").write_text(f'[[index]]\nurl = "{ALIYUN}"\ndefault = true\n')
    outcomes.append(missing_wheel(ALIYUN))
    with pytest.raises(PluginDependencyError, match="404 Not Found"):
        roots.install("fixture", plugin)
    assert len(calls) == 1


@pytest.mark.parametrize("error,after_refresh", [
    (missing_wheel(ALIYUN).replace("404 Not Found", "403 Forbidden"), False),
    (missing_wheel("https://private.example/simple"), False),
    ("No solution found when resolving dependencies", True),
    ("Failed to build fixture: compiler exited with code 1", True),
])
def test_unrelated_errors_do_not_trigger_source_changes(dependency_download, error, after_refresh):
    roots, plugin, calls, outcomes, _ = dependency_download
    if after_refresh:
        outcomes.append(missing_wheel(ALIYUN))
    outcomes.append(error)
    with pytest.raises(PluginDependencyError) as caught:
        roots.install("fixture", plugin)
    assert error in caught.value.detail
    assert len(calls) == 1 + after_refresh
    assert all(env["UV_DEFAULT_INDEX"] == ALIYUN for _, env in calls)


def test_exhausted_sources_preserve_each_failure_and_publish_nothing(dependency_download):
    roots, plugin, calls, outcomes, _ = dependency_download
    errors = [f"failure-{i}\n{missing_wheel(index)}" for i, index in enumerate([ALIYUN, ALIYUN, HUAWEI, PYPI])]
    outcomes.extend(errors)
    with pytest.raises(PluginDependencyError) as caught:
        roots.install("fixture", plugin)
    assert len(calls) == 4
    assert all(error in caught.value.detail for error in errors)
    assert not list(roots._paths.plugin_dependency_roots_dir.iterdir())


def test_failed_recovery_process_preserves_original_download_error(dependency_download):
    roots, plugin, calls, outcomes, _ = dependency_download
    outcomes.extend([missing_wheel(ALIYUN), OSError("could not start uv")])
    with pytest.raises(PluginDependencyError) as caught:
        roots.install("fixture", plugin)
    assert "404 Not Found" in caught.value.detail
    assert "could not start uv" in caught.value.detail
    assert isinstance(caught.value.__cause__, OSError)
    assert len(calls) == 2


@pytest.mark.parametrize("outcome", ["recovered", "exhausted", "process_error"])
def test_dependency_recovery_logs_reach_bridge_with_redacted_diagnostics(dependency_download, outcome):
    from app.core_host.runtime_logging import CORE_BRIDGE_PREFIX, install_runtime_logging

    roots, plugin, _, outcomes, _ = dependency_download
    sensitive = "\nproxy=https://alice:private-pass@proxy.example token=private-token"
    outcomes.extend([missing_wheel(ALIYUN) + sensitive, missing_wheel(ALIYUN)])
    if outcome == "recovered":
        outcomes.append("")
    elif outcome == "exhausted":
        outcomes.extend([missing_wheel(HUAWEI), missing_wheel(PYPI)])
    else:
        outcomes.append(OSError("uv unavailable"))
    stream = io.BytesIO()
    bridge = install_runtime_logging(stream)
    try:
        if outcome == "recovered":
            roots.install("fixture", plugin)
        else:
            with pytest.raises(PluginDependencyError):
                roots.install("fixture", plugin)
    finally:
        bridge.close()
    raw = stream.getvalue()
    records = [json.loads(line[len(CORE_BRIDGE_PREFIX):]) for line in raw.splitlines() if line.startswith(CORE_BRIDGE_PREFIX)]
    assert b"private-pass" not in raw and b"private-token" not in raw
    starts = [record for record in records if record["event"] == "plugin.dependencies.attempt.started"]
    assert [record["attributes"]["dependency_source"] for record in starts] == (
        ["aliyun", "aliyun", "huawei", "pypi"] if outcome == "exhausted" else ["aliyun", "aliyun", "huawei"]
    )
    assert [record["attributes"]["action"] for record in starts[:3]] == ["install", "refresh", "fallback"]
    assert [record["attributes"]["attempt"] for record in starts] == list(range(1, len(starts) + 1))
    assert all(record["severity"] == "info" for record in starts)
    assert all(record["attributes"]["plugin_id"] == "fixture" for record in records)
    assert "404 Not Found" in records[1]["attributes"]["diagnostic"]
    assert records[1]["attributes"]["exit_code"] == 2
    assert records[-1]["event"] == "plugin.dependencies.install." + ("finished" if outcome == "recovered" else "failed")
    assert records[-1]["severity"] == ("info" if outcome == "recovered" else "error")
