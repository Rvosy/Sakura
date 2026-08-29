from __future__ import annotations

from dataclasses import dataclass


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


def append_manual_observation_batch_marker(
    text: str,
    observations: tuple[ScreenObservation, ...],
    visual_id: str | None = None,
) -> str:
    """给一组手动截图追加不包含图像内容的历史标记。"""
    if not observations:
        raise ValueError("manual screen observation batch must not be empty")
    marker = f"[Sakura 已附加 {len(observations)} 张手动框选截图]"
    return f"{text.rstrip()}\n{_marker_with_visual_id(marker, visual_id)}"


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


def build_screen_observation_batch_user_message(
    text: str,
    observations: tuple[ScreenObservation, ...],
) -> dict[str, object]:
    """构造按捕获时间排序的主动截图多模态消息。"""
    if not observations:
        raise ValueError("screen observation batch must not be empty")
    content: list[dict[str, object]] = [{"type": "text", "text": text.strip()}]
    for observation in observations:
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": observation.data_url, "detail": "low"},
            }
        )
    return {"role": "user", "content": content}


def build_manual_screen_observation_batch_user_message(
    text: str,
    observations: tuple[ScreenObservation, ...],
) -> dict[str, object]:
    """构造按用户添加顺序排列的手动截图多模态消息。"""
    if not observations:
        raise ValueError("manual screen observation batch must not be empty")
    details = "\n".join(
        f"截图 {index}：{observation.width}x{observation.height}，"
        f"捕获时间 {observation.captured_at}，屏幕 {observation.screen_name}。"
        for index, observation in enumerate(observations, start=1)
    )
    prompt_text = f"{text.strip()}\n\n以下手动框选截图按添加顺序排列：\n{details}".strip()
    content: list[dict[str, object]] = [{"type": "text", "text": prompt_text}]
    content.extend(
        {
            "type": "image_url",
            "image_url": {"url": observation.data_url, "detail": "low"},
        }
        for observation in observations
    )
    return {"role": "user", "content": content}


def _marker_with_visual_id(marker: str, visual_id: str | None) -> str:
    if not visual_id:
        return marker
    if marker.endswith("]"):
        return f"{marker[:-1]}，视觉记录 visual_id={visual_id}]"
    return f"{marker}，视觉记录 visual_id={visual_id}"
