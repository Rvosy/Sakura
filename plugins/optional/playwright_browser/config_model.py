"""Playwright 插件配置持久化。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class PlaywrightBrowserConfig:
    headless: bool = False

    def clamp(self) -> None:
        self.headless = bool(self.headless)


def default_config_path(plugin_root: Path) -> Path:
    return plugin_root / "config.json"


def load_config(path: Path) -> PlaywrightBrowserConfig:
    if not path.is_file():
        return PlaywrightBrowserConfig()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return PlaywrightBrowserConfig()
        return PlaywrightBrowserConfig(
            headless=bool(raw.get("headless", False)),
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return PlaywrightBrowserConfig()


def config_from_mapping(raw: dict[str, Any]) -> PlaywrightBrowserConfig:
    cfg = PlaywrightBrowserConfig(
        headless=bool(raw.get("headless", False)),
    )
    cfg.clamp()
    return cfg


def config_to_mapping(cfg: PlaywrightBrowserConfig) -> dict[str, Any]:
    cfg.clamp()
    return asdict(cfg)


def save_config(path: Path, cfg: PlaywrightBrowserConfig) -> None:
    cfg.clamp()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(cfg), ensure_ascii=False, indent=2), encoding="utf-8")
