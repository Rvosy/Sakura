from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event, Lock
from typing import Literal

from app.agent.runtime import AgentRuntime
from app.agent.tools import ToolRegistry
from app.agent.trace import AgentTraceRecorder
from app.config.app_version import read_app_version
from app.config.character_loader import (
    CharacterConfigError,
    CharacterProfile,
    CharacterRegistry,
    load_character_system_prompt,
)
from app.config.core_config_reader import CoreConfigReader
from app.core.cancellation import OperationCancelled
from app.core.diagnostics import diagnostic_secret_scope, register_diagnostic_secret
from app.core.chat_pipeline import ChatPipeline
from app.core.runtime_log import diagnostic_attributes, log_event
from app.core_host.character_presentation import project_character_presentation
from app.llm.api_client import OpenAICompatibleClient
from app.storage.runtime_roots import RuntimeRoots, coerce_runtime_roots


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
    current_character_presentation: dict[str, object] | None = None
    session: AssistantSession | None = field(default=None, repr=False)


def project_current_character_summary(profile: CharacterProfile) -> dict[str, object]:
    return {
        "id": profile.id,
        "displayName": profile.display_name,
        "initialMessage": profile.initial_message,
        "replyTones": [*profile.reply_tones],
        "portraitChoices": [],
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


def _report_failure(error: Exception, *, stage: str, code: str) -> None:
    """Record the original failure where it still has its traceback and cause."""
    try:
        log_event(
            "Assistant",
            "Assistant 初始化失败" if stage != "close" else "Assistant 资源关闭失败",
            diagnostic_attributes(error, reason_code=code, stage=stage),
            event="assistant.initialization.failed" if stage != "close" else "assistant.resource.close_failed",
            severity="error",
            verbosity=0,
        )
    except Exception:
        # A broken diagnostic sink must not replace the operation's failure.
        try:
            print(f"Assistant {stage} failed: {type(error).__name__}", file=sys.stderr)
        except Exception:
            pass


def _close_owned(values: list[object]) -> None:
    for value in reversed(values):
        close = getattr(value, "close", None)
        if not callable(close):
            continue
        try:
            close()
        except Exception as error:
            _report_failure(error, stage="close", code="ASSISTANT_RESOURCE_CLOSE_FAILED")


class AssistantAdapter:
    def __init__(
        self,
        roots: RuntimeRoots | Path,
        *,
        tool_registry: ToolRegistry,
        config_reader: CoreConfigReader | None = None,
    ) -> None:
        self._roots = coerce_runtime_roots(roots)
        self._user_root = self._roots.user_root
        self._config_reader = config_reader if config_reader is not None else CoreConfigReader()
        self._lock = Lock()
        self._closed = False
        self._owned: list[object] = []
        # Borrow Application resources; only Provider/Runtime/Pipeline belong to this Session.
        self._application_tools = tool_registry

    @diagnostic_secret_scope()
    def initialize(self, cancel: Event) -> ReadinessResult:
        owned: list[object] = []
        stage = "configuration"
        try:
            self._check_active(cancel)
            config = self._config_reader.read(self._user_root)
            if config.provider_selection is not None:
                register_diagnostic_secret(config.provider_selection.api_settings.api_key)
            self._check_active(cancel)
            if config.config_problem is not None:
                problem = config.config_problem
                presentation = None
                if (
                    problem.code == "PROVIDER_SETUP_REQUIRED"
                    and config.current_character_id is not None
                ):
                    registry = CharacterRegistry(
                        self._user_root,
                        issue_sink=_safe_character_issue_sink,
                    )
                    profile = registry.profiles.get(config.current_character_id)
                    if profile is not None:
                        presentation = project_character_presentation(profile)
                return ReadinessResult(
                    state=problem.state,
                    code=problem.code,
                    message=problem.message,
                    retryable=False,
                    current_character_summary=None,
                    current_character_presentation=presentation,
                )

            stage = "character_registry"
            registry = CharacterRegistry(
                self._user_root,
                issue_sink=_safe_character_issue_sink,
            )
            self._check_active(cancel)
            assert config.provider_selection is not None

            profile = (
                registry.profiles.get(config.current_character_id)
                if config.current_character_id is not None
                else None
            )
            if profile is None:
                return ReadinessResult(
                    state="setup_required",
                    code="CHARACTER_REQUIRED",
                    message="A character must be installed and selected.",
                    retryable=False,
                    current_character_summary=None,
                )

            stage = "model_client"
            trace_recorder = AgentTraceRecorder(self._user_root)
            provider = OpenAICompatibleClient(
                config.provider_selection.api_settings,
                agent_trace_recorder=trace_recorder,
                app_version=read_app_version(self._roots.distribution_root),
            )
            owned.append(provider)
            self._check_active(cancel)

            stage = "character_prompt"
            system_prompt = load_character_system_prompt(profile)
            self._check_active(cancel)
            from app.core_host.tool_settings import load_tool_runtime_configuration

            stage = "runtime_settings"
            runtime_loop_settings = load_tool_runtime_configuration(self._user_root)
            self._check_active(cancel)
            stage = "agent_runtime"
            runtime = AgentRuntime(
                provider,
                system_prompt,
                reply_tones=profile.reply_tones,
                tools=self._application_tools,
                character_id=profile.id,
                character_name=profile.display_name,
                strict_provider_errors=True,
                runtime_loop_settings=runtime_loop_settings,
                agent_trace_recorder=trace_recorder,
            )
            owned.append(runtime)
            self._check_active(cancel)

            stage = "chat_pipeline"
            pipeline = ChatPipeline(runtime, finalize_trace_operations=False)
            owned.append(pipeline)
            self._check_active(cancel)

            session = AssistantSession(
                character=profile,
                provider=provider,
                runtime=runtime,
                pipeline=pipeline,
            )
            self._check_active(cancel)
            if registry.load_errors:
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
                current_character_presentation=project_character_presentation(profile),
                session=session,
            )
            self._check_active(cancel)
            with self._lock:
                if cancel.is_set() or self._closed:
                    raise OperationCancelled()
                self._owned = owned
                owned = []
            return result
        except OperationCancelled:
            _close_owned(owned)
            raise
        except CharacterConfigError as error:
            _report_failure(error, stage=stage, code="CHARACTER_REQUIRED")
            _close_owned(owned)
            return ReadinessResult(
                state="setup_required",
                code="CHARACTER_REQUIRED",
                message="Character setup is required.",
                retryable=False,
                current_character_summary=None,
            )
        except Exception as error:
            _report_failure(error, stage=stage, code="ASSISTANT_INITIALIZATION_FAILED")
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

    def retire_session(self) -> None:
        """Close only Assistant-owned resources while keeping the Core usable."""

        with self._lock:
            if self._closed:
                return
            owned = self._owned
            self._owned = []
        _close_owned(owned)

    def _check_active(self, cancel: Event) -> None:
        if cancel.is_set():
            raise OperationCancelled()
        with self._lock:
            if self._closed:
                raise OperationCancelled()
