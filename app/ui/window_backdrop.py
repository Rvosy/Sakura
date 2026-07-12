from __future__ import annotations

import sys
from typing import Protocol, runtime_checkable

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QWidget

from app.core.runtime_log import log_event


@runtime_checkable
class WindowBackdrop(Protocol):
    """跨平台窗口背景模糊能力接口。"""

    def apply(self, window: QWidget, tint: QColor) -> None: ...

    def remove(self, window: QWidget) -> None: ...

    def supports_native_blur(self) -> bool: ...


# ── 视觉效果模式枚举 ────────────────────────────────────────────────

class VisualEffectMode:
    """输入框/卡片窗口的视觉效果模式。"""

    SOLID = "solid"
    GAUSSIAN_BLUR = "gaussian_blur"
    WINDOWS_ACRYLIC = "windows_acrylic"
    MACOS_VISUAL_EFFECT = "macos_visual_effect"

    _ALL = (SOLID, GAUSSIAN_BLUR, WINDOWS_ACRYLIC, MACOS_VISUAL_EFFECT)
    DEFAULT = GAUSSIAN_BLUR

    @classmethod
    def available_modes(cls) -> list[str]:
        modes = [cls.SOLID, cls.GAUSSIAN_BLUR]
        if sys.platform == "darwin":
            modes.append(cls.MACOS_VISUAL_EFFECT)
        return modes

    @classmethod
    def validate(cls, value: str) -> str:
        return value if value in cls._ALL else cls.DEFAULT


# ── 工厂 ─────────────────────────────────────────────────────────────

def create_window_backdrop(mode: str | None = None) -> WindowBackdrop:
    if mode is None:
        if sys.platform == "win32":
            build = _windows_build()
            if build >= 17134:
                return WindowsAcrylicBackdrop(rounded=build >= 22000)
        if sys.platform == "darwin":
            return MacOSVisualEffectBackdrop()
        return FallbackTintBackdrop()

    mode = VisualEffectMode.validate(mode)
    if mode == VisualEffectMode.SOLID:
        return FallbackTintBackdrop()
    if mode == VisualEffectMode.GAUSSIAN_BLUR:
        return SoftwareBlurBackdrop()
    if mode == VisualEffectMode.WINDOWS_ACRYLIC:
        build = _windows_build()
        return WindowsAcrylicBackdrop(rounded=build >= 22000) if build >= 17134 else FallbackTintBackdrop()
    if mode == VisualEffectMode.MACOS_VISUAL_EFFECT:
        return MacOSVisualEffectBackdrop() if sys.platform == "darwin" else FallbackTintBackdrop()
    return FallbackTintBackdrop()


def _windows_build() -> int:
    try:
        return int(sys.getwindowsversion().build)  # type: ignore[attr-defined]
    except Exception:
        return 0


# ── 实现 ──────────────────────────────────────────────────────────────

class FallbackTintBackdrop:
    """无系统级模糊的平台降级：apply/remove 为空操作。"""

    def apply(self, window: QWidget, tint: QColor) -> None:
        pass

    def remove(self, window: QWidget) -> None:
        pass

    def supports_native_blur(self) -> bool:
        return False


class MacOSVisualEffectBackdrop:
    """macOS 原生毛玻璃（NSVisualEffectView with HUDWindow material）。

    直接将 NSVisualEffectView 添加到 NSWindow.contentView 最底层。
    使用 HUDWindow 材质提供显著可见的暗色毛玻璃效果。
    """

    _MATERIAL = 13  # NSVisualEffectMaterialHUDWindow — 显著的暗色毛玻璃
    _BLENDING = 0   # NSVisualEffectBlendingModeBehindWindow
    _STATE = 1      # NSVisualEffectStateActive — 强制始终 active；0 是 FollowsWindowActiveState，
                    # 会在窗口非 key 时渲染为扁平灰白色（不采样背景模糊）
    _NS_WINDOW_BELOW = -1

    def __init__(self) -> None:
        self._effect_view: object | None = None
        self._fallback = FallbackTintBackdrop()

    def apply(self, window: QWidget, tint: QColor) -> None:
        if sys.platform != "darwin":
            return
        if self._effect_view is not None:
            self._sync_effect_view_frame(window)
            return
        try:
            from AppKit import NSVisualEffectView

            root_view = self._root_view_for_window(window)

            # root_view (QNSView) 自身就是 contentView。
            # 若把 NSVisualEffectView 添加为 contentView 的子视图，它在 Qt 直接绘制
            # 内容之上会遮挡所有文字和控件。
            # 必须添加到 root_view 的父视图（NSNextStepFrame），排在 root_view 之下。
            superview = root_view.superview()
            if superview is None:
                self._fallback.apply(window, tint)
                return

            # 对齐 root_view 在其父视图中的位置和尺寸（root_view 可能不在 (0,0)）
            effect_view = NSVisualEffectView.alloc().initWithFrame_(
                self._frame_for_root_view(root_view)
            )
            if effect_view is None:
                self._fallback.apply(window, tint)
                return

            effect_view.setMaterial_(self._MATERIAL)
            effect_view.setBlendingMode_(self._BLENDING)
            effect_view.setState_(self._STATE)
            effect_view.setAutoresizingMask_(2 | 16)
            effect_view.setWantsLayer_(True)
            # 与 InputBlurBackground 的 corner_radius 保持一致
            effect_view.setCornerRadius_(22.0)

            # sibling injection: 插入到 root_view 下面，Qt 内容透过
            # WA_TranslucentBackground 的透明区域可见毛玻璃
            superview.addSubview_positioned_relativeTo_(
                effect_view, self._NS_WINDOW_BELOW, root_view,
            )

            self._effect_view = effect_view

        except Exception as exc:
            log_event("UI", "macOS NSVisualEffectView 创建失败，降级为半透明", {"error": str(exc)})
            self._fallback.apply(window, tint)

    def _sync_effect_view_frame(self, window: QWidget) -> None:
        if self._effect_view is None:
            return
        try:
            root_view = self._root_view_for_window(window)
            self._effect_view.setFrame_(self._frame_for_root_view(root_view))
        except Exception as exc:  # noqa: BLE001
            log_event("UI", "macOS NSVisualEffectView 尺寸同步失败", {"error": str(exc)})

    def _root_view_for_window(self, window: QWidget) -> object:
        from ctypes import c_void_p

        import objc

        return objc.objc_object(c_void_p=c_void_p(int(window.winId())))

    def _frame_for_root_view(self, root_view: object) -> object:
        from Foundation import NSMakeRect

        rv_frame = root_view.frame()
        return NSMakeRect(
            rv_frame.origin.x,
            rv_frame.origin.y,
            rv_frame.size.width,
            rv_frame.size.height,
        )

    def remove(self, window: QWidget) -> None:
        if self._effect_view is not None:
            try:
                self._effect_view.removeFromSuperview()
            except Exception:
                pass
        self._effect_view = None

    def supports_native_blur(self) -> bool:
        return True


class SoftwareBlurBackdrop:
    """软件截图模糊标记：不施加任何系统级模糊。

    apply/remove 均为空操作。圆角与背景完全由 InputBlurBackground 负责。
    """

    def apply(self, window: QWidget, tint: QColor) -> None:
        pass

    def remove(self, window: QWidget) -> None:
        pass

    def supports_native_blur(self) -> bool:
        return False


class WindowsAcrylicBackdrop:
    """Windows 亚克力背景模糊（DWM 合成器实时模糊）。"""

    _WCA_ACCENT_POLICY = 19
    _ACCENT_ENABLE_ACRYLICBLURBEHIND = 4
    _ACCENT_DISABLED = 0
    _DWMWA_WINDOW_CORNER_PREFERENCE = 33
    _DWMWCP_DONOTROUND = 1
    _DWMWCP_ROUND = 2

    def __init__(self, *, rounded: bool) -> None:
        self._rounded = rounded

    def apply(self, window: QWidget, tint: QColor) -> None:
        try:
            hwnd = int(window.winId())
            self._set_accent(hwnd, self._ACCENT_ENABLE_ACRYLICBLURBEHIND, tint)
            if self._rounded:
                self._set_round_corners(hwnd)
        except Exception as exc:
            log_event("UI", "Windows 亚克力背景应用失败，降级为半透明", {"error": str(exc)})

    def remove(self, window: QWidget) -> None:
        try:
            hwnd = int(window.winId())
            self._set_accent(hwnd, self._ACCENT_DISABLED, QColor(0, 0, 0, 0))
            if self._rounded:
                self._set_corner_preference(hwnd, self._DWMWCP_DONOTROUND)
        except Exception as exc:
            log_event("UI", "Windows 亚克力背景移除失败", {"error": str(exc)})

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
        self._set_corner_preference(hwnd, self._DWMWCP_ROUND)

    def _set_corner_preference(self, hwnd: int, preference_value: int) -> None:
        import ctypes
        preference = ctypes.c_int(preference_value)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, self._DWMWA_WINDOW_CORNER_PREFERENCE,
            ctypes.byref(preference), ctypes.sizeof(preference),
        )


def _gradient_color(tint: QColor) -> int:
    return (
        (tint.alpha() << 24)
        | (tint.blue() << 16)
        | (tint.green() << 8)
        | tint.red()
    )
