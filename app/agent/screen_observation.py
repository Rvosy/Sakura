from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtGui import QImage
    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import QWidget


SCREEN_OBSERVATION_TRIGGER_KEYWORDS = (
    "看屏幕",
    "观察屏幕",
    "看看屏幕",
    "看看当前画面",
    "帮我看这个",
)
SCREEN_OBSERVATION_HISTORY_MARKER = "[Sakura 已自主观察屏幕]"
MANUAL_SCREEN_OBSERVATION_HISTORY_MARKER = "[Sakura 已附加手动框选截图]"
SCREEN_OBSERVATION_MAX_EDGE = 1280
SCREEN_OBSERVATION_JPEG_QUALITY = 70


@dataclass(frozen=True)
class ScreenObservation:
    """一次按需屏幕观察结果，不负责持久化截图内容。"""

    data_url: str
    width: int
    height: int
    captured_at: str
    screen_name: str


@dataclass(frozen=True)
class CapturedScreenImage:
    """UI 线程捕获的屏幕图像；后续压缩编码可放到后台线程。"""

    image: QImage
    captured_at: str
    screen_name: str


def should_observe_screen(text: str) -> bool:
    """判断用户是否明确要求观察屏幕。"""
    normalized = "".join(text.split()).lower()
    return any(keyword in normalized for keyword in SCREEN_OBSERVATION_TRIGGER_KEYWORDS)


def append_observation_marker(
    text: str,
    observation: ScreenObservation,
    visual_id: str | None = None,
) -> str:
    """给历史记录追加观察标记，避免保存 base64 图片。"""
    _ = observation
    return f"{text.rstrip()}\n{_marker_with_visual_id(SCREEN_OBSERVATION_HISTORY_MARKER, visual_id)}"


def append_manual_observation_marker(
    text: str,
    observation: ScreenObservation,
    visual_id: str | None = None,
) -> str:
    """给手动框选截图追加历史标记，避免保存 base64 图片。"""
    _ = observation
    return f"{text.rstrip()}\n{_marker_with_visual_id(MANUAL_SCREEN_OBSERVATION_HISTORY_MARKER, visual_id)}"


def build_screen_observation_user_message(
    text: str,
    observation: ScreenObservation,
) -> dict[str, object]:
    """构造 OpenAI 兼容的多模态用户消息。"""
    prompt_text = (
        f"{text.strip()}\n\n"
        f"当前屏幕截图信息：{observation.width}x{observation.height}，"
        f"捕获时间 {observation.captured_at}，屏幕 {observation.screen_name}。"
    ).strip()
    return {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": prompt_text,
            },
            {
                "type": "image_url",
                "image_url": {
                    "url": observation.data_url,
                    "detail": "low",
                },
            },
        ],
    }


def capture_screen_image(excluded_widget: QWidget | None = None) -> CapturedScreenImage:
    """截取光标所在屏幕并复制为 QImage，避免后台线程触碰 QPixmap。"""

    from PySide6.QtGui import QCursor
    from PySide6.QtWidgets import QApplication

    _ = excluded_widget
    app = QApplication.instance()
    if app is None:
        raise RuntimeError("屏幕观察需要先创建 QApplication。")

    screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
    if screen is None:
        raise RuntimeError("无法找到可截图的屏幕。")

    pixmap = screen.grabWindow(0)

    if pixmap.isNull():
        raise RuntimeError("屏幕截图为空，可能被系统权限或显示环境阻止。")

    return CapturedScreenImage(
        image=pixmap.toImage().copy(),
        captured_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        screen_name=screen.name() or "primary",
    )


def capture_screen_observation(excluded_widget: QWidget | None = None) -> ScreenObservation:
    """同步截屏并编码；UI 调用优先使用 capture_screen_image + 后台编码。"""
    return build_screen_observation_from_image(capture_screen_image(excluded_widget))


def build_screen_observation_from_image(captured: CapturedScreenImage) -> ScreenObservation:
    """从已复制的 QImage 构造观察结果，可在后台线程执行。"""
    if captured.image.isNull():
        raise RuntimeError("屏幕截图为空。")

    encoded_image = _scaled_image(captured.image)
    return ScreenObservation(
        data_url=_encode_image_to_data_url(encoded_image),
        width=encoded_image.width(),
        height=encoded_image.height(),
        captured_at=captured.captured_at,
        screen_name=captured.screen_name,
    )


def build_screen_observation_from_pixmap(
    pixmap: QPixmap,
    screen_name: str = "manual-selection",
) -> ScreenObservation:
    """从用户框选区域构造一次屏幕观察结果。"""
    if pixmap.isNull():
        raise RuntimeError("框选截图为空。")

    return build_screen_observation_from_image(
        CapturedScreenImage(
            image=pixmap.toImage().copy(),
            captured_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            screen_name=screen_name,
        )
    )


def _scaled_image(image: QImage) -> QImage:
    from PySide6.QtCore import Qt

    longest_edge = max(image.width(), image.height())
    if longest_edge <= SCREEN_OBSERVATION_MAX_EDGE:
        return image
    return image.scaled(
        SCREEN_OBSERVATION_MAX_EDGE,
        SCREEN_OBSERVATION_MAX_EDGE,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def _encode_image_to_data_url(image: QImage) -> str:
    from PySide6.QtCore import QBuffer, QIODevice

    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "JPEG", SCREEN_OBSERVATION_JPEG_QUALITY):
        raise RuntimeError("屏幕截图编码失败。")
    image_bytes = bytes(buffer.data())
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def _scaled_pixmap(pixmap: QPixmap) -> QPixmap:
    from PySide6.QtCore import Qt

    longest_edge = max(pixmap.width(), pixmap.height())
    if longest_edge <= SCREEN_OBSERVATION_MAX_EDGE:
        return pixmap
    return pixmap.scaled(
        SCREEN_OBSERVATION_MAX_EDGE,
        SCREEN_OBSERVATION_MAX_EDGE,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def _encode_pixmap_to_data_url(pixmap: QPixmap) -> str:
    from PySide6.QtCore import QBuffer, QIODevice

    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not pixmap.toImage().save(buffer, "JPEG", SCREEN_OBSERVATION_JPEG_QUALITY):
        raise RuntimeError("屏幕截图编码失败。")
    image_bytes = bytes(buffer.data())
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def _marker_with_visual_id(marker: str, visual_id: str | None) -> str:
    if not visual_id:
        return marker
    if marker.endswith("]"):
        return f"{marker[:-1]}，视觉记录 visual_id={visual_id}]"
    return f"{marker}，视觉记录 visual_id={visual_id}"
