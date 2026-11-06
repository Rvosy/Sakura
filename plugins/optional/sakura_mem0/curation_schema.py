"""记忆整理只接受有明确操作类型和完整内容的结果。"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

try:
    from .memory import DEFAULT_MEMORY_CONFIDENCE, DEFAULT_MEMORY_IMPORTANCE
except ImportError:
    from memory import DEFAULT_MEMORY_CONFIDENCE, DEFAULT_MEMORY_IMPORTANCE


class CurationOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    reason: str = ""


class AddMemory(CurationOperation):
    op: Literal["add"]
    content: str = Field(min_length=1)
    layer: Literal["semantic", "episodic", "procedural", "session", "core_profile"] = "semantic"
    category: str = ""
    importance: float = Field(default=DEFAULT_MEMORY_IMPORTANCE, ge=0, le=1)
    confidence: float = Field(default=DEFAULT_MEMORY_CONFIDENCE, ge=0, le=1)


class UpdateMemory(AddMemory):
    op: Literal["update"]
    id: str = Field(min_length=1)


class DeleteMemory(CurationOperation):
    op: Literal["delete"]
    id: str = Field(min_length=1)


class CurationOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operations: list[AddMemory | UpdateMemory | DeleteMemory]
