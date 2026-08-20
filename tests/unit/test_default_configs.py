from __future__ import annotations

from pathlib import Path

import yaml

from app.config.default_configs import ensure_default_configs
from app.storage.paths import StoragePaths


def test_default_mcp_config_has_web_and_macos_but_no_windows_server(tmp_path: Path) -> None:
    ensure_default_configs(tmp_path)

    document = yaml.safe_load(StoragePaths(tmp_path).mcp_config().read_text(encoding="utf-8"))

    assert set(document["servers"]) == {"web", "macos"}


def test_existing_builtin_windows_server_is_retired_without_touching_other_servers(
    tmp_path: Path,
) -> None:
    path = StoragePaths(tmp_path).mcp_config()
    path.parent.mkdir(parents=True)
    path.write_text(
        """\
enabled: true
servers:
  web:
    transport: stdio
    command: python
  windows:
    transport: stdio
    command: uv
  custom:
    transport: sse
    url: https://example.invalid/mcp
""",
        encoding="utf-8",
    )

    ensure_default_configs(tmp_path)
    document = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert set(document["servers"]) == {"web", "custom", "macos"}
    assert document["servers"]["custom"]["url"] == "https://example.invalid/mcp"
