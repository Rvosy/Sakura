from __future__ import annotations

from pathlib import Path

import pytest

from app.plugin_sdk.sakura_downloads import DEFAULT_PYPI_INDEX, uv_download_environment
from app.plugins.dependencies import PluginDependencyRoots


def test_default_index_and_proxy_reach_plugin_install_process(tmp_path, monkeypatch):
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    (plugin / "requirements.txt").write_text("fixture==1.0\n", encoding="utf-8")
    # Isolate explicit configuration from the developer's environment.
    monkeypatch.setattr("app.plugins.dependencies.uv_download_environment", lambda path: uv_download_environment(path, {}))
    monkeypatch.setattr("urllib.request.getproxies", lambda: {"https": "http://127.0.0.1:7890"})
    captured = []

    def run(command, **kwargs):
        from subprocess import CompletedProcess
        captured.append(kwargs["env"])
        return CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("app.plugins.dependencies.subprocess.run", run)
    root = PluginDependencyRoots(tmp_path / "user").install("example.plugin", plugin)
    assert root is not None
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
