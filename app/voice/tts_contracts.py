"""Qt-free Runtime v2 TTS contracts.

Provider protocol, deployment endpoint and public errors live here so callers
never need to import a concrete engine or process supervisor.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol


class TtsErrorCode(str, Enum):
    PROVIDER_NOT_FOUND = "PROVIDER_NOT_FOUND"
    INVALID_CONFIGURATION = "INVALID_CONFIGURATION"
    RUNTIME_START_FAILED = "RUNTIME_START_FAILED"
    RUNTIME_UNAVAILABLE = "RUNTIME_UNAVAILABLE"
    CONNECTION_FAILED = "CONNECTION_FAILED"
    REQUEST_TIMEOUT = "REQUEST_TIMEOUT"
    SYNTHESIS_FAILED = "SYNTHESIS_FAILED"
    INVALID_AUDIO_RESPONSE = "INVALID_AUDIO_RESPONSE"
    REFERENCE_AUDIO_UNAVAILABLE = "REFERENCE_AUDIO_UNAVAILABLE"


class TtsError(RuntimeError):
    def __init__(
        self,
        code: TtsErrorCode | str,
        public_message: str,
        *,
        retryable: bool = False,
        source: BaseException | None = None,
    ) -> None:
        self.code = str(getattr(code, "value", code))
        self.public_message = public_message
        self.retryable = bool(retryable)
        self.source = source
        super().__init__(f"{self.code}: {public_message}")


@dataclass(frozen=True)
class TtsRequest:
    text: str
    language: str | None = None
    character_id: str | None = None
    tone: str | None = None


@dataclass(frozen=True)
class TtsAudio:
    data: bytes
    media_type: str = "audio/wav"


@dataclass(frozen=True)
class ResolvedTtsEndpoint:
    base_url: str
    tts_path: str
    kind: str
    lifecycle_owned: bool

    @property
    def synthesis_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/{self.tts_path.lstrip('/')}"


class TtsProvider(Protocol):
    provider_id: str

    def ensure_ready(self) -> tuple[bool, str]: ...

    def synthesize(self, request: TtsRequest) -> TtsAudio: ...

    def close(self) -> None: ...


class ManagedTtsRuntime(Protocol):
    def ensure_running(self) -> tuple[bool, str]: ...

    def close(self) -> None: ...


def error_code_from_message(message: object, default: str = "SYNTHESIS_FAILED") -> str:
    text = str(message)
    for code in TtsErrorCode:
        if code.value in text:
            return code.value
    return default


def relative_reference_path(reference: Path, package_dir: Path) -> Path:
    try:
        return reference.resolve(strict=False).relative_to(package_dir.resolve(strict=False))
    except (OSError, ValueError) as exc:
        raise TtsError(
            TtsErrorCode.REFERENCE_AUDIO_UNAVAILABLE,
            "参考音频不在当前角色包内，无法映射到远程服务。",
            source=exc,
        ) from exc
