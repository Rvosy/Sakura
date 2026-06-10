from __future__ import annotations

import sys
from typing import Protocol, runtime_checkable

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QWidget

from app.core.debug_log import debug_log


@runtime_checkable
class WindowBackdrop(Protocol):
    """跨平台窗口背景模糊能力接口。

    apply 把系统级背景模糊（如 Windows 亚克力）施加到一个**已显示**的顶层窗口，
    让窗口透明区透出并模糊背后的真实桌面。不支持的平台用降级实现，保证调用统一、不报错。
    """

    def apply(self, window: QWidget, tint: QColor) -> None: ...

    def remove(self, window: QWidget) -> None: ...

    def supports_native_blur(self) -> bool: ...


def create_window_backdrop() -> WindowBackdrop:
    """按当前平台与系统版本探测，返回最合适的背景模糊实现。"""
    if sys.platform == "win32":
        build = _windows_build()
        if build >= 17134:  # Windows 10 1803+ 起支持亚克力
            return WindowsAcrylicBackdrop(rounded=build >= 22000)
    if sys.platform == "darwin":
        return MacOSVisualEffectBackdrop()
    # Linux/旧 Windows 降级占位
    return FallbackTintBackdrop()


def _windows_build() -> int:
    try:
        return int(sys.getwindowsversion().build)  # type: ignore[attr-defined]
    except Exception:
        return 0


class FallbackTintBackdrop:
    """无系统级模糊的平台（Mac/Linux/旧 Windows）降级占位。

    不做真模糊：卡片自身的半透明 QSS 背景即降级效果，这里只作为接口占位，
    apply/remove 为空操作，保证上层调用统一、不报错。
    """

    def apply(self, window: QWidget, tint: QColor) -> None:
        del window, tint

    def remove(self, window: QWidget) -> None:
        del window

    def supports_native_blur(self) -> bool:
        return False


class MacOSVisualEffectBackdrop:
    """macOS 原生 NSVisualEffectView 毛玻璃背景。

    使用 PyObjC 桥接 AppKit（而非 ctypes），因为 ctypes 在 arm64 上无法正确按值
    传递 NSRect（HFA-4）等 AppKit struct 给 objc_msgSend。
    PyObjC 内置完整的 Objective-C 类型编码支持，能够正确处理所有 struct 传参。

    参照 pyqt-liquidglass (https://github.com/kotikotprojects/pyqt-liquidglass) 方案：
    1. 用 objc.objc_object 获取 Qt 窗口的 NSView/NSWindow
    2. 创建 NSVisualEffectView 并添加到 contentView
    3. 配置窗口透明属性（setOpaque, clearColor, titlebarAppearsTransparent）
    4. 把 effect view 放在 Qt 内容之下

    任何调用失败都静默降级到 FallbackTintBackdrop。
    """

    def __init__(self) -> None:
        self._effect_view: object | None = None
        self._fallback = FallbackTintBackdrop()

    def apply(self, window: QWidget, tint: QColor) -> None:
        if sys.platform != "darwin":
            return
        # 幂等：已创建过就不再重复添加
        if self._effect_view is not None:
            return
        try:
            from ctypes import c_void_p

            import objc  # pyobjc-core
            from AppKit import (
                NSColor,
                NSVisualEffectBlendingModeBehindWindow,
                NSVisualEffectMaterialPopover,
                NSVisualEffectStateActive,
                NSVisualEffectView,
            )
            from Foundation import NSMakeRect

            # 1. 用 PyObjC 获取 Qt 窗口的 NSView
            win_id = int(window.winId())
            ns_view = objc.objc_object(c_void_p=c_void_p(win_id))

            # 2. 获取 NSWindow
            ns_window = ns_view.window()
            if ns_window is None:
                self._fallback.apply(window, tint)
                return

            # 3. 获取 contentView
            content_view = ns_window.contentView()
            if content_view is None:
                self._fallback.apply(window, tint)
                return

            # 4. 创建 NSVisualEffectView，使用窗口尺寸
            frame_w = float(window.width())
            frame_h = float(window.height())
            frame = NSMakeRect(0.0, 0.0, frame_w, frame_h)

            effect_view = NSVisualEffectView.alloc().initWithFrame_(frame)
            if effect_view is None:
                self._fallback.apply(window, tint)
                return

            effect_view.setMaterial_(NSVisualEffectMaterialPopover)
            effect_view.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
            effect_view.setState_(NSVisualEffectStateActive)
            effect_view.setAutoresizingMask_(2 | 16)  # NSViewWidthSizable | NSViewHeightSizable

            # 5. 配置 NSWindow 透明属性（让毛玻璃可以透过窗口显示）
            ns_window.setOpaque_(False)
            ns_window.setBackgroundColor_(NSColor.clearColor())
            ns_window.setTitlebarAppearsTransparent_(True)
            # 启用全屏内容视图 (NSFullSizeContentViewWindowMask = 1 << 15)
            current_mask = ns_window.styleMask()
            ns_window.setStyleMask_(current_mask | (1 << 15))

            # 6. 把 effect view 放在 contentView 最底层（Qt 内容在上面）
            # NSViewBelow = -1 (NSWindowBelow), 0 = nil
            content_view.addSubview_positioned_relativeTo_(effect_view, -1, None)

            self._effect_view = effect_view

        except Exception as exc:  # noqa: BLE001
            debug_log("UI", "macOS NSVisualEffectView 创建失败，降级为半透明", {"error": str(exc)})
            self._fallback.apply(window, tint)

    def remove(self, window: QWidget) -> None:
        del window
        if self._effect_view is not None:
            try:
                self._effect_view.removeFromSuperview()
            except Exception:  # noqa: BLE001
                pass
            finally:
                self._effect_view = None

    def supports_native_blur(self) -> bool:
        return True


class SoftwareBlurBackdrop:
    """软件截图模糊背景标记：不施加任何系统级模糊。

    输入栏改用软件自截图 + 高斯模糊 + 自绘大圆角（见 app/ui/input_blur_background.py），
    DWM 亚克力是窗口级合成、做不出大圆角，故这里把窗口从亚克力路径摘下：apply/remove 均为空操作，
    圆角与背景完全由 InputBlurBackground 负责。supports_native_blur 返回 False（它是静态截图，非实时）。
    """

    def apply(self, window: QWidget, tint: QColor) -> None:
        del window, tint

    def remove(self, window: QWidget) -> None:
        del window

    def supports_native_blur(self) -> bool:
        return False


class WindowsAcrylicBackdrop:
    """Windows 亚克力背景模糊（DWM 合成器实时模糊窗口背后的真实桌面）。

    主路径：user32.SetWindowCompositionAttribute + ACCENT_ENABLE_ACRYLICBLURBEHIND，
    Win10 1803+ / Win11 通用；Win11 额外用 DwmSetWindowAttribute 设原生圆角。
    任何调用失败都静默降级（不影响窗口正常显示）。
    """

    _WCA_ACCENT_POLICY = 19
    _ACCENT_ENABLE_ACRYLICBLURBEHIND = 4
    _ACCENT_DISABLED = 0
    _DWMWA_WINDOW_CORNER_PREFERENCE = 33
    _DWMWCP_ROUND = 2

    def __init__(self, *, rounded: bool) -> None:
        self._rounded = rounded

    def apply(self, window: QWidget, tint: QColor) -> None:
        # 亚克力是 DWM 窗口级合成，无视 Qt setMask/SetWindowRgn，圆角只能交给 DWM 原生圆角。
        try:
            hwnd = int(window.winId())
            self._set_accent(hwnd, self._ACCENT_ENABLE_ACRYLICBLURBEHIND, tint)
            if self._rounded:
                self._set_round_corners(hwnd)
        except Exception as exc:  # noqa: BLE001
            debug_log("UI", "Windows 亚克力背景应用失败，降级为半透明", {"error": str(exc)})

    def remove(self, window: QWidget) -> None:
        try:
            hwnd = int(window.winId())
            self._set_accent(hwnd, self._ACCENT_DISABLED, QColor(0, 0, 0, 0))
        except Exception as exc:  # noqa: BLE001
            debug_log("UI", "Windows 亚克力背景移除失败", {"error": str(exc)})

    def supports_native_blur(self) -> bool:
        return True

    def _set_accent(self, hwnd: int, accent_state: int, tint: QColor) -> None:
        import ctypes

        class ACCENT_POLICY(ctypes.Structure):
            _fields_ = [
                ("AccentState", ctypes.c_int),
                ("AccentFlags", ctypes.c_int),
                ("GradientColor", ctypes.c_uint),
                ("AnimationId", ctypes.c_int),
            ]

        class WINDOWCOMPOSITIONATTRIBDATA(ctypes.Structure):
            _fields_ = [
                ("Attribute", ctypes.c_int),
                ("Data", ctypes.POINTER(ACCENT_POLICY)),
                ("SizeOfData", ctypes.c_size_t),
            ]

        accent = ACCENT_POLICY()
        accent.AccentState = accent_state
        accent.AccentFlags = 0
        accent.GradientColor = _gradient_color(tint)
        accent.AnimationId = 0

        data = WINDOWCOMPOSITIONATTRIBDATA()
        data.Attribute = self._WCA_ACCENT_POLICY
        data.SizeOfData = ctypes.sizeof(accent)
        data.Data = ctypes.pointer(accent)

        ctypes.windll.user32.SetWindowCompositionAttribute(hwnd, ctypes.pointer(data))

    def _set_round_corners(self, hwnd: int) -> None:
        import ctypes

        preference = ctypes.c_int(self._DWMWCP_ROUND)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd,
            self._DWMWA_WINDOW_CORNER_PREFERENCE,
            ctypes.byref(preference),
            ctypes.sizeof(preference),
        )


def _gradient_color(tint: QColor) -> int:
    """QColor → 亚克力 GradientColor 的 0xAABBGGRR 整数（磨砂底色 + alpha）。"""
    return (
        (tint.alpha() << 24)
        | (tint.blue() << 16)
        | (tint.green() << 8)
        | tint.red()
    )
