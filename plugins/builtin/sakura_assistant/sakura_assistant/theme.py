"""根据角色图片生成主题，使用普通模型服务及其取消和资源回收。"""
from __future__ import annotations

from typing import Annotated

from pydantic import ConfigDict, Field, create_model
from sakura_model import ModelClient, ModelError
from sakura_structured_output import load_json_object, require_complete_response, response_format

from .llm.prompts.recipes import build_theme_color_system_prompt


THEME_FIELDS = {
    "primary_color": "primary",
    "primary_hover_color": "primaryHover",
    "accent_color": "accent",
    "text_color": "text",
    "secondary_text_color": "secondaryText",
    "muted_text_color": "mutedText",
    "page_background_color": "pageBackground",
    "panel_background_color": "panelBackground",
    "input_background_color": "inputBackground",
    "bubble_background_color": "bubbleBackground",
    "border_color": "border",
}
ThemeColors = create_model(
    "ThemeColors",
    __config__=ConfigDict(extra="forbid"),
    **{key: (Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")], ...) for key in THEME_FIELDS},
)


def generate_theme(context, request, *, cancel_checker):
    client = ModelClient(context, request["model"], usage_category="theme", usage_label="AI 生成配色")
    try:
        modalities = client.description.get("inputModalities")
        if modalities is not None and "image" not in modalities:
            raise ModelError("MODEL_IMAGE_UNSUPPORTED", "当前模型不支持图片，请在模型设置中选择支持图片的视觉对话模型。")
        result = client.complete({
            "messages": [
                {"role": "system", "content": build_theme_color_system_prompt(request["characterName"])},
                {"role": "user", "content": [
                    {"type": "text", "text": "根据这张角色图片生成完整的 UI 配色 JSON。"},
                    {"type": "image_url", "image_url": {"url": request["imageUrl"], "detail": "low"}},
                ]},
            ],
            "parameters": {"temperature": 0.2},
            "responseFormat": response_format("sakura_theme", ThemeColors.model_json_schema()),
        }, cancel_checker=cancel_checker)
        require_complete_response(result)
        data, _ = load_json_object(result["message"].get("content") or "")
        colors = ThemeColors.model_validate(data).model_dump()
        return {field: colors[key].lower() for key, field in THEME_FIELDS.items()}
    finally:
        client.close()
