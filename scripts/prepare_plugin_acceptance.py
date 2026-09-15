"""Create a separate, verified user root for manual plugin-ecosystem acceptance."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from threading import Event
from uuid import uuid4


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from app.agent.tools import ToolRegistry
from app.core_host.assistant_adapter import AssistantAdapter
from app.core_host.plugin_application import PluginApplicationHost
from app.plugins.installer import LocalPluginInstaller
from app.plugins.inventory import PluginDesiredStateStore, PluginInventory
from app.storage.paths import StoragePaths
from app.storage.runtime_roots import RuntimeRoots
from app.storage.timeline import TimelineStore


CHARACTER_ID = "sakura-acceptance"
ENABLED_PLUGINS = {"sakura.portrait"}


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _powershell_literal(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _write_launchers(user_root: Path) -> tuple[Path, Path]:
    executable = REPOSITORY_ROOT / "desktop/src-tauri/target/debug/sakura.exe"
    manifest = REPOSITORY_ROOT / "desktop/src-tauri/Cargo.toml"
    build_command = f"cargo build --manifest-path {_powershell_literal(manifest)} --locked"
    script = f"""$ErrorActionPreference = 'Stop'
$acceptanceRepository = {_powershell_literal(REPOSITORY_ROOT)}
$acceptanceUserRoot = {_powershell_literal(user_root)}
$acceptanceExecutable = {_powershell_literal(executable)}
if (-not (Test-Path -LiteralPath $acceptanceExecutable -PathType Leaf)) {{
    Write-Host '尚未找到 Sakura 开发版，请先在仓库中编译：'
    Write-Host {_powershell_literal(build_command)}
    exit 1
}}
if (@(Get-Process -Name 'sakura' -ErrorAction SilentlyContinue).Count -gt 0) {{
    Write-Host '请先从托盘菜单退出正在运行的 Sakura，再启动验收环境。应用只允许一个实例。'
    exit 1
}}
$env:SAKURA_RUNTIME_USER_ROOT = $acceptanceUserRoot
Set-Location -LiteralPath $acceptanceRepository
Write-Host ('验收数据目录：' + $acceptanceUserRoot)
& $acceptanceExecutable
if ($null -ne $LASTEXITCODE) {{ exit $LASTEXITCODE }}
"""
    ps1 = user_root / "启动验收.ps1"
    # Windows PowerShell 5.1 requires a BOM for Chinese source text.
    ps1.write_text(script, encoding="utf-8-sig")
    bat = user_root / "启动验收.bat"
    bat.write_text(
        '@echo off\nchcp 65001 >nul\nsetlocal DisableDelayedExpansion\n'
        'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0启动验收.ps1"\n'
        'if errorlevel 1 pause\n',
        encoding="utf-8",
    )
    return ps1, bat


def _verify(roots: RuntimeRoots) -> dict[str, object]:
    registry = ToolRegistry()
    application = PluginApplicationHost(roots, "plugin-acceptance-" + uuid4().hex, registry)
    adapter = AssistantAdapter(roots, tool_registry=registry, mcp_provider=None)
    try:
        application.start()
        readiness = adapter.initialize(Event())
        if readiness.state != "setup_required" or readiness.code != "PROVIDER_SETUP_REQUIRED":
            raise RuntimeError(f"空白验收环境应等待模型配置：{readiness.code}")
        application.bind_character_presentation(CHARACTER_ID)
        presentation = application.visual_presentation()
        visual = presentation.get("visual") if isinstance(presentation, dict) else None
        if not isinstance(visual, dict) or visual.get("providerId") != "sakura.portrait":
            raise RuntimeError("验收角色立绘未就绪。")
        active = sorted(
            record["pluginId"] for record in application.public_snapshot()["plugins"]
            if record["state"] == "active"
        )
        if set(active) != ENABLED_PLUGINS:
            raise RuntimeError(f"验收环境启用了非预期的插件：{active}")
        return {
            "assistantReadiness": readiness.state,
            "readinessCode": readiness.code,
            "providerConfigured": False,
            "activePlugins": active,
            "portraitProvider": visual["providerId"],
            "portraitMetadata": visual["data"]["metadata"],
            "guiVerified": False,
        }
    finally:
        adapter.close()
        application.close()


def prepare(user_root: Path) -> dict[str, object]:
    user_root = user_root.expanduser()
    if user_root.exists() or user_root.is_symlink():
        raise FileExistsError(user_root)
    user_root = user_root.resolve(strict=False)
    icon = REPOSITORY_ROOT / "desktop/frontend/assets/sakura-icon.png"
    sources = [REPOSITORY_ROOT / "plugins/optional/context_rules"]
    if not icon.is_file() or any(not (source / "plugin.yaml").is_file() for source in sources):
        raise RuntimeError("仓库缺少验收所需的插件或 Sakura 图标。")
    # Never reuse a directory, including an existing empty directory or symlink.
    user_root.mkdir(parents=True, exist_ok=False)
    roots = RuntimeRoots(REPOSITORY_ROOT, user_root)
    paths = StoragePaths(user_root)
    paths.config_dir.mkdir()
    paths.system_config().write_text("config_version: 1\n", encoding="utf-8")
    paths.characters_config().write_text(f"current_character_id: {CHARACTER_ID}\n", encoding="utf-8")

    character_root = paths.characters_dir / CHARACTER_ID
    character_root.mkdir(parents=True)
    shutil.copyfile(icon, character_root / "portrait.png")
    (character_root / "card.md").write_text(
        "你是 Sakura，正在陪用户体验本地插件。回答自然、简短；用户要求详细说明时再展开。\n",
        encoding="utf-8",
    )
    _write_json(character_root / "character.json", {
        "id": CHARACTER_ID,
        "display_name": "Sakura 验收",
        "card": "card.md",
        "initial_message": "我在。准备好后就开始吧。",
        "portrait": {"default": "portrait.png", "expressions": {}},
        "reply": {"tones": ["中性"]},
    })

    installer = LocalPluginInstaller(roots)
    for source in sources:
        installer.install(source, "folder")
    desired = PluginDesiredStateStore(user_root)
    inventory = PluginInventory(roots, desired).scan()
    desired.write({
        record.plugin_id: record.plugin_id in ENABLED_PLUGINS
        for record in inventory.records if record.plugin_id is not None
    })
    timeline = TimelineStore(paths.timeline_database())
    timeline.initialize()
    ps1, bat = _write_launchers(user_root)
    verification = _verify(roots)
    if timeline.read_all(CHARACTER_ID):
        raise RuntimeError("准备验收环境时意外写入了对话历史。")
    report = {
        "createdAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "distributionRoot": str(REPOSITORY_ROOT),
        "userRoot": str(user_root),
        "launchPowerShell": str(ps1),
        "launchBatch": str(bat),
        "timeline": str(paths.timeline_database()),
        "verification": verification,
    }
    _write_json(user_root / "acceptance-environment.json", report)
    return report


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="准备空白插件验收目录，检查默认对话配置状态和立绘加载。")
    parser.add_argument("--user-root", type=Path, help="新目录的路径；已存在时拒绝写入。")
    args = parser.parse_args()
    user_root = args.user_root or (
        REPOSITORY_ROOT / "temp" / f"plugin-acceptance-{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:8]}"
    )
    try:
        report = prepare(user_root)
    except FileExistsError:
        parser.exit(1, f"目录已存在，未覆盖：{user_root}\n")
    except Exception as error:
        parser.exit(1, f"准备失败：{error}\n新建的目录保留在 {user_root}，便于检查。\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
