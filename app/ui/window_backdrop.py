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

    通过 ctypes 调用 Objective-C runtime 创建 NSVisualEffectView 并添加到窗口内容视图。
    material 使用 Popover（弹出窗口风格），blendingMode 使用 BehindWindow（模糊窗口后方内容）。
    任何调用失败都静默降级到 FallbackTintBackdrop。
    """

    _NS_VISUAL_EFFECT_MATERIAL_POPOVER = 10
    _NS_VISUAL_EFFECT_BLENDING_BEHIND_WINDOW = 0
    _NS_VISUAL_EFFECT_STATE_ACTIVE = 0

    def __init__(self) -> None:
        self._effect_view: object | None = None
        self._fallback = FallbackTintBackdrop()

    def apply(self, window: QWidget, tint: QColor) -> None:
        if sys.platform != "darwin":
            return
        try:
            import ctypes
            import ctypes.util

            objc = ctypes.CDLL(ctypes.util.find_library("objc") or "/usr/lib/libobjc.A.dylib")

            # 设置 objc_msgSend 签名
            objc.objc_getClass.restype = ctypes.c_void_p
            objc.objc_getClass.argtypes = [ctypes.c_char_p]
            objc.sel_registerName.restype = ctypes.c_void_p
            objc.sel_registerName.argtypes = [ctypes.c_char_p]

            def msg_send(obj, sel_name, *args):
                sel = objc.sel_registerName(sel_name.encode())
                if not args:
                    objc.objc_msgSend.restype = ctypes.c_void_p
                    objc.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
                    return objc.objc_msgSend(obj, sel)
                else:
                    argtypes = [ctypes.c_void_p, ctypes.c_void_p] + [type(a) for a in args]
                    objc.objc_msgSend.restype = ctypes.c_void_p
                    objc.objc_msgSend.argtypes = argtypes
                    return objc.objc_msgSend(obj, sel, *args)

            # 获取窗口的 NSWindow
            win_id = int(window.winId())
            # Qt 的 winId() 在 macOS 上返回 NSView*，需要获取其 window
            ns_view = ctypes.c_void_p(win_id)
            ns_window = msg_send(ns_view, "window")
            if not ns_window:
                self._fallback.apply(window, tint)
                return

            # 获取 contentView
            content_view = msg_send(ctypes.c_void_p(ns_window), "contentView")
            if not content_view:
                self._fallback.apply(window, tint)
                return

            # 创建 NSVisualEffectView
            ns_visual_effect_class = objc.objc_getClass(b"NSVisualEffectView")
            if not ns_visual_effect_class:
                self._fallback.apply(window, tint)
                return

            # alloc + initWithFrame:
            alloc = msg_send(ns_visual_effect_class, "alloc")
            # 使用窗口的 bounds 作为 frame
            bounds = msg_send(ctypes.c_void_p(content_view), "bounds")
            # bounds 是 NSRect (struct)，需要特殊处理
            # 简化：使用 initWithFrame:NSZeroRect，然后 setAutoresizingMask
            zero_rect = (ctypes.c_double * 4)(0, 0, 0, 0)
            objc.objc_msgSend.restype = ctypes.c_void_p
            objc.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
            effect_view = objc.objc_msgSend(
                alloc,
                objc.sel_registerName(b"initWithFrame:"),
                zero_rect,
            )
            if not effect_view:
                self._fallback.apply(window, tint)
                return

            self._effect_view = ctypes.c_void_p(effect_view)

            # setAutoresizingMask: NSViewWidthSizable | NSViewHeightSizable
            msg_send(self._effect_view, "setAutoresizingMask:", ctypes.c_ulong(2 | 16))

            # setMaterial: NSVisualEffectMaterialPopover
            msg_send(
                self._effect_view,
                "setMaterial:",
                ctypes.c_long(self._NS_VISUAL_EFFECT_MATERIAL_POPOVER),
            )

            # setBlendingMode: NSVisualEffectBlendingModeBehindWindow
            msg_send(
                self._effect_view,
                "setBlendingMode:",
                ctypes.c_long(self._NS_VISUAL_EFFECT_BLENDING_BEHIND_WINDOW),
            )

            # setState: NSVisualEffectStateActive
            msg_send(
                self._effect_view,
                "setState:",
                ctypes.c_long(self._NS_VISUAL_EFFECT_STATE_ACTIVE),
            )

            # addSubview: 添加到 contentView
            msg_send(ctypes.c_void_p(content_view), "addSubview:", self._effect_view)

            # 确保 effect view 在最底层（其他内容在上）
            msg_send(self._effect_view, "setWantsLayer:", ctypes.c_bool(True))

        except Exception as exc:  # noqa: BLE001
            debug_log("UI", "macOS NSVisualEffectView 创建失败，降级为半透明", {"error": str(exc)})
            self._fallback.apply(window, tint)

    def remove(self, window: QWidget) -> None:
        if self._effect_view is not None:
            try:
                import ctypes
                objc = ctypes.CDLL(ctypes.util.find_library("objc") or "/usr/lib/libobjc.A.dylib")
                objc.sel_registerName.restype = ctypes.c_void_p
                objc.sel_registerName.argtypes = [ctypes.c_char_p]
                objc.objc_msgSend.restype = ctypes.c_void_p
                objc.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
                sel = objc.sel_registerName(b"removeFromSuperview")
                objc.objc_msgSend(self._effect_view, sel)
                self._effect_view = None
            except Exception:  # noqa: BLE001
                pass

    def supports_native_blur(self) -> bool:
        return sys.platform == "darwin"


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
