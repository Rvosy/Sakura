from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event, Lock
from typing import Literal

from app.agent.runtime import AgentRuntime
from app.agent.tools import ToolRegistry
from app.config.character_loader import (
    DEFAULT_CHARACTER_ID,
    CharacterConfigError,
    CharacterProfile,
    CharacterRegistry,
    load_character_system_prompt,
)
from app.config.core_config_reader import CoreConfigReader
from app.core.cancellation import OperationCancelled
from app.core.chat_pipeline import ChatPipeline
from app.llm.api_client import OpenAICompatibleClient


@dataclass
class AssistantSession:
    character: CharacterProfile
    provider: OpenAICompatibleClient = field(repr=False)
    runtime: AgentRuntime
    pipeline: ChatPipeline


@dataclass(frozen=True)
class ReadinessResult:
    state: Literal["ready", "setup_required", "degraded", "failed"]
    code: str
    message: str
    retryable: bool
    current_character_summary: dict[str, object] | None
    session: AssistantSession | None = field(default=None, repr=False)


class DisabledMemory:
    def __bool__(self) -> bool:
        return True

    def search_memory(
        self,
        _payload: dict[str, object],
        *,
        wait: bool = False,
    ) -> dict[str, object]:
        return {"status": "disabled", "memories": []}

    def summary(self) -> str:
        return ""

    def close(self) -> None:
        return None


def project_current_character_summary(profile: CharacterProfile) -> dict[str, object]:
    return {
        "id": profile.id,
        "displayName": profile.display_name,
        "initialMessage": profile.initial_message,
        "replyTones": [*profile.reply_tones],
        "portraitChoices": [*profile.portrait_choices],
    }


def _safe_character_issue_sink(
    _scope: str,
    _message: str,
    _details: dict[str, object],
) -> None:
    try:
        print("A character package was skipped during initialization.", file=sys.stderr)
    except Exception:
        pass


def _safe_close_issue() -> None:
    try:
        print("An Assistant resource failed to close cleanly.", file=sys.stderr)
    except Exception:
        pass


def _close_owned(values: list[object]) -> None:
    for value in reversed(values):
        close = getattr(value, "close", None)
        if not callable(close):
            continue
        try:
            close()
        except Exception:
            _safe_close_issue()


class AssistantAdapter:
    def __init__(self, app_root: Path, *, config_reader: CoreConfigReader | None = None) -> None:
        self._app_root = Path(app_root)
        self._config_reader = config_reader if config_reader is not None else CoreConfigReader()
        self._lock = Lock()
        self._closed = False
        self._owned: list[object] = []

    def initialize(self, cancel: Event) -> ReadinessResult:
        owned: list[object] = []
        try:
            self._check_active(cancel)
            config = self._config_reader.read(self._app_root)
            self._check_active(cancel)
            if config.config_problem is not None:
                problem = config.config_problem
                return ReadinessResult(
                    state=problem.state,
                    code=problem.code,
                    message=problem.message,
                    retryable=False,
                    current_character_summary=None,
                )

            registry = CharacterRegistry(
                self._app_root,
                issue_sink=_safe_character_issue_sink,
            )
            self._check_active(cancel)
            assert config.current_character_id is not None
            assert config.provider_selection is not None

            profile = registry.profiles.get(config.current_character_id)
            fallback_applied = profile is None
            if profile is None:
                profile = registry.profiles.get(DEFAULT_CHARACTER_ID)
            if profile is None:
                profile = registry.all()[0]

            provider = OpenAICompatibleClient(config.provider_selection.api_settings)
            owned.append(provider)
            self._check_active(cancel)

            tools = ToolRegistry([])
            owned.append(tools)
            self._check_active(cancel)

            memory = DisabledMemory()
            owned.append(memory)
            self._check_active(cancel)

            system_prompt = load_character_system_prompt(profile)
            self._check_active(cancel)
            runtime = AgentRuntime(
                provider,
                system_prompt,
                reply_tones=profile.reply_tones,
                reply_portraits=profile.portrait_choices,
                tools=tools,
                memory=memory,
                character_id=profile.id,
                character_name=profile.display_name,
            )
            owned.append(runtime)
            self._check_active(cancel)

            pipeline = ChatPipeline(runtime)
            owned.append(pipeline)
            self._check_active(cancel)

            session = AssistantSession(
                character=profile,
                provider=provider,
                runtime=runtime,
                pipeline=pipeline,
            )
            if fallback_applied:
                state = "degraded"
                code = "CHARACTER_FALLBACK_APPLIED"
                message = "Configured character was unavailable; a fallback was applied."
            elif registry.load_errors:
                state = "degraded"
                code = "OPTIONAL_CHARACTER_SKIPPED"
                message = "An optional character package was skipped."
            else:
                state = "ready"
                code = "READY"
                message = "Assistant session is ready."
            result = ReadinessResult(
                state=state,
                code=code,
                message=message,
                retryable=False,
                current_character_summary=project_current_character_summary(profile),
                session=session,
            )
            with self._lock:
                if self._closed:
                    raise OperationCancelled()
                self._owned = owned
                owned = []
            return result
        except OperationCancelled:
            _close_owned(owned)
            raise
        except CharacterConfigError:
            _close_owned(owned)
            return ReadinessResult(
                state="setup_required",
                code="CHARACTER_SETUP_REQUIRED",
                message="Character setup is required.",
                retryable=False,
                current_character_summary=None,
            )
        except Exception:
            _close_owned(owned)
            return ReadinessResult(
                state="failed",
                code="ASSISTANT_INITIALIZATION_FAILED",
                message="Assistant initialization failed.",
                retryable=False,
                current_character_summary=None,
            )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            owned = self._owned
            self._owned = []
        _close_owned(owned)

    def _check_active(self, cancel: Event) -> None:
        if cancel.is_set():
            raise OperationCancelled()
        with self._lock:
            if self._closed:
                raise OperationCancelled()
