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
        """当前平台可用的模式列表。"""
        modes = [cls.SOLID, cls.GAUSSIAN_BLUR]
        if sys.platform == "win32" and _windows_build() >= 17134:
            modes.append(cls.WINDOWS_ACRYLIC)
        if sys.platform == "darwin":
            modes.append(cls.MACOS_VISUAL_EFFECT)
        return modes

    @classmethod
    def validate(cls, value: str) -> str:
        if value in cls._ALL:
            return value
        return cls.DEFAULT


def create_window_backdrop(mode: str | None = None) -> WindowBackdrop:
    """按指定模式（或平台探测）返回最合适的背景实现。

    mode 为空时走平台自动探测（旧行为，保持兼容）。
    """
    if mode is None:
        # 平台自动探测：mac → VisualEffect, win → Acrylic, 其他 → Fallback
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

    **为什么要用 Content Swap 而不是直接改 NSWindow 透明属性？**

    如果直接设置 NSWindow.setOpaque_(False) + setBackgroundColor_(clearColor())，
    整个窗口的 backing store 进入 ARGB 全透明模式。Qt 在 macOS 上通过 CoreText/Metal
    渲染文字和控件时，依赖窗口背景做 alpha 混合。背景全透明 → premultiplied alpha
    全部归零 → 所有内容肉眼不可见（毛玻璃可见，内容消失）。

    Content Swap 方案：
    1. 创建一个新的透明 NSView 作为容器
    2. 替换 NSWindow 的 contentView
    3. 把 Qt 的 root_view 和 NSVisualEffectView 都加入容器
    4. effect view 放在 root_view 下面（sibling 关系，不是父子）
    5. 容器自身透明，但 NSWindow 不透明 → Qt 渲染正常

    任何调用失败都静默降级到 FallbackTintBackdrop。
    """

    def __init__(self) -> None:
        self._effect_view: object | None = None
        self._container: object | None = None
        self._original_content_view: object | None = None
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
                NSView,
                NSVisualEffectBlendingModeBehindWindow,
                NSVisualEffectMaterialUnderWindowBackground,
                NSVisualEffectStateActive,
                NSVisualEffectView,
            )
            from Foundation import NSMakeRect

            # 1. 用 PyObjC 获取 Qt 窗口的 NSView（root_view）
            win_id = int(window.winId())
            root_view = objc.objc_object(c_void_p=c_void_p(win_id))

            # 2. 获取 NSWindow 和 contentView
            ns_window = root_view.window()
            if ns_window is None:
                self._fallback.apply(window, tint)
                return

            content_view = ns_window.contentView()
            if content_view is None:
                self._fallback.apply(window, tint)
                return

            # ── Content Swap ──
            # 创建一个新的透明容器，替换 NSWindow 的 contentView，
            # 然后把 Qt 的 root_view 和 NSVisualEffectView 都加入新容器。
            from Foundation import NSMakeRect

            frame_w = float(window.width())
            frame_h = float(window.height())
            container_frame = NSMakeRect(0.0, 0.0, frame_w, frame_h)

            container = NSView.alloc().initWithFrame_(container_frame)
            if container is None:
                self._fallback.apply(window, tint)
                return

            container.setAutoresizingMask_(2 | 16)  # NSViewWidthSizable | NSViewHeightSizable
            container.setWantsLayer_(True)

            # 保存原始 contentView，用于 remove 时恢复
            self._original_content_view = content_view

            # 替换 contentView
            ns_window.setContentView_(container)

            # 把 Qt 的 root_view 加入容器（撑满）
            root_view.setFrame_(container.bounds())
            root_view.setAutoresizingMask_(2 | 16)
            container.addSubview_(root_view)

            # ── 创建 NSVisualEffectView ──
            effect_frame = NSMakeRect(0.0, 0.0, frame_w, frame_h)
            effect_view = NSVisualEffectView.alloc().initWithFrame_(effect_frame)
            if effect_view is None:
                self._fallback.apply(window, tint)
                return

            # 使用 UnderWindowBackground 材质（窗口背景毛玻璃），
            # 比 Popover 更适合作为窗口底色。
            effect_view.setMaterial_(NSVisualEffectMaterialUnderWindowBackground)
            effect_view.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
            effect_view.setState_(NSVisualEffectStateActive)
            effect_view.setAutoresizingMask_(2 | 16)

            # ── 关键：effect view 放在 root_view 下面 ──
            # 使用 sibling 关系：effect view 是 container 的子视图，排在 root_view 之下。
            # NSWindowBelow = -1
            container.addSubview_positioned_relativeTo_(effect_view, -1, root_view)

            self._effect_view = effect_view
            self._container = container

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
        # 恢复原始 contentView
        if self._original_content_view is not None and self._container is not None:
            try:
                # 从 container 中找到 NSWindow
                ns_window = self._container.window()
                if ns_window is not None:
                    ns_window.setContentView_(self._original_content_view)
            except Exception:  # noqa: BLE001
                pass
            finally:
                self._container = None
                self._original_content_view = None

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
