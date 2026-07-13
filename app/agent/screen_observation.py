from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

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
SCREEN_OBSERVATION_MAX_BYTES = 24 * 1024 * 1024


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


def build_screen_observation_from_private_resource(
    resource: Mapping[str, Any],
    *,
    base_dir: Path,
) -> ScreenObservation:
    """读取 Rust 生成的受控 JPEG，并在离开本函数前删除临时文件。"""

    raw_path = resource.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError("截图资源缺少 path。")
    capture_root = (Path(base_dir).resolve() / "data" / "cache" / "captures").resolve()
    try:
        path = Path(raw_path).resolve(strict=True)
        path.relative_to(capture_root)
    except (OSError, ValueError) as exc:
        raise ValueError("截图资源必须位于受控截图目录。") from exc
    if not path.is_file():
        raise ValueError("截图资源不是普通文件。")

    try:
        mime_type = resource.get("mimeType", resource.get("mime_type"))
        if mime_type != "image/jpeg" or path.suffix.casefold() not in {".jpg", ".jpeg"}:
            raise ValueError("截图资源必须是 JPEG。")
        width = _positive_resource_int(resource, "width")
        height = _positive_resource_int(resource, "height")
        captured_at = _resource_text(resource, "capturedAt", "captured_at")
        screen_name = _resource_text(resource, "screenName", "screen_name") or "screen"
        if path.stat().st_size > SCREEN_OBSERVATION_MAX_BYTES:
            raise ValueError("截图资源超过大小限制。")
        image_bytes = path.read_bytes()
        if not image_bytes.startswith(b"\xff\xd8\xff") or not image_bytes.endswith(b"\xff\xd9"):
            raise ValueError("截图资源内容不是有效 JPEG。")
    finally:
        path.unlink(missing_ok=True)
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return ScreenObservation(
        data_url=f"data:image/jpeg;base64,{encoded}",
        width=width,
        height=height,
        captured_at=captured_at or datetime.now().astimezone().isoformat(timespec="seconds"),
        screen_name=screen_name,
    )


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


def build_screen_observation_from_image(
    captured: CapturedScreenImage,
    *,
    max_edge: int = SCREEN_OBSERVATION_MAX_EDGE,
    max_width: int | None = None,
    max_height: int | None = None,
) -> ScreenObservation:
    """从已复制的 QImage 构造观察结果，可在后台线程执行。"""
    if captured.image.isNull():
        raise RuntimeError("屏幕截图为空。")

    encoded_image = _scaled_image(
        captured.image,
        max_edge=max_edge,
        max_width=max_width,
        max_height=max_height,
    )
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


def _scaled_image(
    image: QImage,
    *,
    max_edge: int = SCREEN_OBSERVATION_MAX_EDGE,
    max_width: int | None = None,
    max_height: int | None = None,
) -> QImage:
    from PySide6.QtCore import Qt

    if max_width is not None and max_height is not None:
        target_width = max(1, int(max_width))
        target_height = max(1, int(max_height))
        if image.width() <= target_width and image.height() <= target_height:
            return image
        return image.scaled(
            target_width,
            target_height,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    max_edge = max(1, int(max_edge or SCREEN_OBSERVATION_MAX_EDGE))
    longest_edge = max(image.width(), image.height())
    if longest_edge <= max_edge:
        return image
    return image.scaled(
        max_edge,
        max_edge,
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


def _positive_resource_int(resource: Mapping[str, Any], key: str) -> int:
    try:
        value = int(resource.get(key, 0))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"截图资源 {key} 无效。") from exc
    if value <= 0:
        raise ValueError(f"截图资源 {key} 无效。")
    return value


def _resource_text(resource: Mapping[str, Any], snake_case: str, camel_case: str) -> str:
    value = resource.get(snake_case, resource.get(camel_case, ""))
    return value.strip() if isinstance(value, str) else ""
