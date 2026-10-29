"""自动整理和文本提取共用的记忆结果结构。"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


EntityKind = Literal["", "person", "project", "place", "thing", "event", "topic"]


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    entry_id: str = Field(min_length=1)
    quote: str = Field(min_length=1)


class AddMemory(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    op: Literal["add"]
    content: str = Field(min_length=1)
    layer: Literal["core_profile", "procedural", "semantic", "episodic", "session"]
    evidence: list[Evidence]
    state: Literal["active", "completed", "cancelled"] = "active"
    category: str = ""
    subject: str = ""
    subject_kind: EntityKind = ""
    predicate: str = ""
    target: str = ""
    target_kind: EntityKind = ""


class UpdateMemory(AddMemory):
    op: Literal["update"]
    id: str = Field(min_length=1)


class CurationOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operations: list[AddMemory | UpdateMemory]
