"""聊天回复的字段定义，供模型请求和结果解析共用。"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from sakura_structured_output import response_format


class ReplySegment(BaseModel):
    model_config = ConfigDict(strict=True, str_strip_whitespace=True)

    ja: str = Field(min_length=1, validation_alias=AliasChoices("ja", "japanese", "text"))
    zh: str = Field(min_length=1, validation_alias=AliasChoices("zh", "chinese", "translation"))
    tone: str = "中性"
    portrait: str = ""
    control: dict[str, Any] | None = None

    @field_validator("tone", "portrait", mode="before")
    @classmethod
    def optional_text(cls, value, info):
        return cls.model_fields[info.field_name].default if value is None else value

    @field_validator("control", mode="before")
    @classmethod
    def optional_control(cls, value):
        # 表现数据损坏不阻断已经可用的文字回复。
        return value if isinstance(value, dict) else None


class ReplyOutput(BaseModel):
    segments: list[ReplySegment] = Field(min_length=1)


class VisualObservation(BaseModel):
    summary: str
    visible_texts: list[str]
    uncertain_texts: list[str]
    notable_elements: list[str]
    confidence: float
    sensitive_redacted: bool


def reply_schema(tones=None, visual=None, *, include_observation=False):
    schema = ReplyOutput.model_json_schema()
    segment = schema["$defs"]["ReplySegment"]
    fields = segment["properties"]
    fields.pop("portrait")
    fields.pop("control")
    if tones:
        fields["tone"]["enum"] = list(tones)
    segment.update(required=["ja", "zh", "tone"], additionalProperties=False)
    schema["additionalProperties"] = False
    if visual:
        fields["control"] = {
            "type": "object", "additionalProperties": False,
            "required": ["version", "resourceId", "payload"],
            "properties": {
                "version": {"type": "integer", "const": 1},
                "resourceId": {"type": "string", "const": visual["resourceId"]},
                "payload": deepcopy(visual["outputSchema"]),
            },
        }
        segment["required"].append("control")
    if include_observation:
        schema["properties"]["visual_observation"] = VisualObservation.model_json_schema()
        schema["required"].append("visual_observation")
    return schema


def reply_response_format(tones=None, visual=None, *, include_observation=False):
    # 表现插件的可选字段表示保持原状态，不能为了 strict 模式强制生成动作。
    return response_format("sakura_reply", reply_schema(tones, visual, include_observation=include_observation), strict=not bool(visual))
