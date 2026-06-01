"""Playwright 插件设置页 — 挂载到 Tools 标签页。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from plugins.playwright_browser import browser
from plugins.playwright_browser.config_model import (
    BROWSER_CHOICES,
    PlaywrightBrowserConfig,
    default_config_path,
    load_config,
    save_config,
)


_BROWSER_LABELS = {
    "chromium": "Chromium（Playwright 内置，需下载）",
    "firefox": "Firefox（Playwright 内置，需下载）",
    "webkit": "WebKit（Playwright 内置，需下载）",
    "msedge": "Microsoft Edge（使用系统已安装的 Edge）",
    "chrome": "Google Chrome（使用系统已安装的 Chrome）",
}


class PlaywrightBrowserSettingsTab(QWidget):
    def __init__(self, plugin_root: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._root = plugin_root
        self._cfg_path = default_config_path(plugin_root)

        lay = QVBoxLayout(self)

        box = QGroupBox("Playwright 浏览器设置")
        form = QFormLayout(box)

        cfg = load_config(self._cfg_path)

        self._browser_combo = QComboBox()
        for key in BROWSER_CHOICES:
            self._browser_combo.addItem(_BROWSER_LABELS.get(key, key), key)
        idx = self._browser_combo.findData(cfg.browser_type)
        if idx >= 0:
            self._browser_combo.setCurrentIndex(idx)
        self._browser_combo.currentIndexChanged.connect(self._on_save)
        form.addRow("浏览器类型", self._browser_combo)

        hint = QLabel(
            "Chromium/Firefox/WebKit 需要 playwright install 下载；"
            "Edge/Chrome 使用系统浏览器无需下载。修改后需重启生效。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid);")
        form.addRow(hint)

        self._headless_cb = QCheckBox("无头模式（Headless）")
        self._headless_cb.setChecked(bool(cfg.headless))
        self._headless_cb.toggled.connect(self._on_save)
        form.addRow(self._headless_cb)

        lay.addWidget(box)
        lay.addStretch(1)

    def _on_save(self) -> None:
        cfg = PlaywrightBrowserConfig(
            headless=self._headless_cb.isChecked(),
            browser_type=str(self._browser_combo.currentData() or "chromium"),
        )
        save_config(self._cfg_path, cfg)
        browser.set_plugin_root(str(self._root))
