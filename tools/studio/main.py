"""SakuraCharacterStudio 入口。

负责三件事：
1. 把项目根注入 sys.path，使本工具能直接复用主项目的 app.* 模块；
2. 复刻主程序 main.py 的 QApplication 初始化（Fusion 亮色调色板 + Windows
   DPI 感知），保证编辑器与桌宠观感一致；
3. 创建并显示 StudioWindow。

两种启动方式均可：
    python -m tools.studio.main
    python tools/studio/main.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# 注入项目根，确保无论以模块还是脚本方式启动都能 import app.* / tools.studio.*
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtCore import Qt, QtMsgType, qInstallMessageHandler
from PySide6.QtGui import QColor, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication, QStyleFactory

from tools.studio.app_studio import StudioWindow


def _qt_message_handler(msg_type: QtMsgType, context: object, msg: str) -> None:
    # 与主程序一致：丢弃 Windows 无边框透明窗口触发的无害 DWM 边框警告
    if "setDarkBorderToWindow" in msg:
        return
    sys.stderr.write(f"{msg}\n")
    if msg_type == QtMsgType.QtFatalMsg:
        sys.exit(1)


def _force_light_palette(app: QApplication) -> None:
    """强制 Fusion 风格 + 亮色 palette，避免 Windows 暗色模式下系统控件文字与浅背景冲突。

    与主程序 main.py `_force_light_palette` 保持一致，让 Studio 的原生控件观感统一。
    """
    app.setStyle(QStyleFactory.create("Fusion"))
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#fff6fa"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#3d2b35"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#fff6fa"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#3d2b35"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#3d2b35"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#ffe8f1"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#fff6fa"))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#3d2b35"))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor("#9b4f72"))
    app.setPalette(palette)


def _configure_windows_high_dpi() -> None:
    """在 QApplication 创建前配置 Windows 混合 DPI 行为（精简自主程序）。"""
    if sys.platform != "win32":
        return

    import ctypes

    # 进程级 DPI 感知，按 Per-Monitor V2 → Per-Monitor → System 逐级降级
    for attempt in (
        lambda: ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)),
        lambda: ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0,
        lambda: ctypes.windll.user32.SetProcessDPIAware(),
    ):
        try:
            if attempt():
                break
        except Exception:  # noqa: BLE001 - DPI 配置失败不应阻断启动
            continue

    try:
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    except Exception:  # noqa: BLE001
        pass


def main() -> int:
    qInstallMessageHandler(_qt_message_handler)
    _configure_windows_high_dpi()

    app = QApplication(sys.argv)
    app.setApplicationName("SakuraCharacterStudio")
    _force_light_palette(app)

    window = StudioWindow(project_root=PROJECT_ROOT)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
