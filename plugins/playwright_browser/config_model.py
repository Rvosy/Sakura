"""Playwright 插件配置持久化。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


BROWSER_CHOICES = ("chromium", "firefox", "webkit", "msedge", "chrome")


@dataclass
class PlaywrightBrowserConfig:
    headless: bool = False
    browser_type: str = "msedge"

    def clamp(self) -> None:
        if self.browser_type not in BROWSER_CHOICES:
            self.browser_type = "msedge"


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
            browser_type=str(raw.get("browser_type", "msedge")).strip().lower(),
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return PlaywrightBrowserConfig()


def save_config(path: Path, cfg: PlaywrightBrowserConfig) -> None:
    cfg.clamp()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(cfg), ensure_ascii=False, indent=2), encoding="utf-8")
