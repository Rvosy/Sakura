from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event, Lock
from time import monotonic
from typing import Literal

from app.agent.runtime import AgentRuntime
from app.agent.trace import AgentTraceRecorder, normalize_agent_trace_settings
from app.config.character_loader import (
    DEFAULT_CHARACTER_ID,
    CharacterConfigError,
    CharacterProfile,
    CharacterRegistry,
    load_character_system_prompt,
)
from app.config.core_config_reader import CoreConfigReader
from app.config.yaml_config import load_yaml_mapping
from app.core.cancellation import OperationCancelled
from app.core.chat_pipeline import ChatPipeline
from app.core_host.character_presentation import project_character_presentation
from app.llm.api_client import OpenAICompatibleClient


@dataclass
class AssistantSession:
    character: CharacterProfile
    provider: OpenAICompatibleClient = field(repr=False)
    runtime: AgentRuntime
    pipeline: ChatPipeline
    mcp_provider: object | None = field(default=None, repr=False)

    def wait_prompt_dependencies(
        self,
        *,
        cancel_checker=None,
        mcp_timeout: float = 15.0,
    ) -> list[dict[str, object]]:
        results: list[dict[str, object]] = []
        dependency_wait_started = monotonic()
        mcp_wait = getattr(self.mcp_provider, "wait_registration", None)
        if callable(mcp_wait):
            started = monotonic()
            remaining = max(
                0.0,
                mcp_timeout - (monotonic() - dependency_wait_started),
            )
            completed = mcp_wait(remaining, cancel_checker=cancel_checker)
            snapshot = self.mcp_provider.status_snapshot()
            reason = str(snapshot.get("reasonCode", "UNKNOWN"))
            results.append(
                {
                    "dependency": "mcp",
                    "ready": completed
                    and reason in {"READY", "CONFIG_DISABLED", "CONFIG_MISSING"},
                    "status": "ready"
                    if completed and reason in {"READY", "CONFIG_DISABLED", "CONFIG_MISSING"}
                    else ("degraded" if completed else "loading"),
                    "reason_code": reason if completed else "REGISTRATION_TIMEOUT",
                    "elapsed_ms": round((monotonic() - started) * 1000),
                }
            )
        return results


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
        self._tools_enabled = False
        self._mcp_enabled = False
        self._plugins_enabled = False
        self._generation_id = ""
        self._owned: list[object] = []
        self._application_tools: object | None = None
        self._application_mcp: object | None = None

    def enable_tools(self) -> None:
        """Enable Core-owned tools before initialization starts."""

        with self._lock:
            if self._closed:
                raise OperationCancelled()
            self._tools_enabled = True

    def enable_mcp(self) -> None:
        """Enable the generation-private MCP owner before initialization starts."""

        with self._lock:
            if self._closed:
                raise OperationCancelled()
            self._mcp_enabled = True

    def enable_plugins(self) -> None:
        """Enable the generation-private plugin worker before initialization."""

        with self._lock:
            if self._closed:
                raise OperationCancelled()
            self._plugins_enabled = True

    def bind_generation(self, generation_id: str) -> None:
        with self._lock:
            if self._closed or not generation_id.strip():
                raise OperationCancelled()
            self._generation_id = generation_id

    def bind_application_resources(
        self,
        tool_registry: object,
        mcp_provider: object | None = None,
    ) -> None:
        """Use generation-owned resources without taking lifecycle ownership."""

        with self._lock:
            if self._closed:
                raise OperationCancelled()
            self._application_tools = tool_registry
            self._application_mcp = mcp_provider

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

            trace_settings = normalize_agent_trace_settings(
                load_yaml_mapping(
                    self._app_root / "data" / "config" / "system_config.yaml"
                ).get("agent_trace")
            )
            trace_recorder = AgentTraceRecorder(self._app_root, trace_settings)
            provider = OpenAICompatibleClient(
                config.provider_selection.api_settings,
                agent_trace_recorder=trace_recorder,
            )
            owned.append(provider)
            self._check_active(cancel)

            system_prompt = load_character_system_prompt(profile)
            self._check_active(cancel)
            with self._lock:
                tools_enabled = self._tools_enabled
                mcp_enabled = self._mcp_enabled
            from app.core_host.tool_settings import load_tool_runtime_configuration

            runtime_loop_settings = load_tool_runtime_configuration(self._app_root)
            if self._application_tools is not None:
                tools = self._application_tools
            elif tools_enabled:
                from app.core_host.tools import create_runtime_v2_tool_registry

                tools = create_runtime_v2_tool_registry()
                owned.append(tools)
            else:
                from app.agent.tools import ToolRegistry

                tools = ToolRegistry([])
                owned.append(tools)
            mcp_provider: object | None = None
            if mcp_enabled:
                mcp_provider = self._application_mcp
                if mcp_provider is None:
                    from app.agent.mcp.provider import start_mcp_tools_from_config
                    from app.core.runtime_resources import ResourceRegistry
                    from app.core_host.mcp_settings import load_mcp_runtime_settings

                    mcp_provider = start_mcp_tools_from_config(
                        self._app_root,
                        tools,
                        runtime_settings=load_mcp_runtime_settings(self._app_root),
                        resource_registry=ResourceRegistry(),
                    )
                    owned.append(mcp_provider)
            self._check_active(cancel)
            runtime = AgentRuntime(
                provider,
                system_prompt,
                reply_tones=profile.reply_tones,
                reply_portraits=profile.portrait_choices,
                tools=tools,
                character_id=profile.id,
                character_name=profile.display_name,
                strict_provider_errors=True,
                runtime_loop_settings=runtime_loop_settings,
                agent_trace_recorder=trace_recorder,
            )
            owned.append(runtime)
            self._check_active(cancel)

            pipeline = ChatPipeline(runtime, finalize_trace_operations=False)
            owned.append(pipeline)
            self._check_active(cancel)

            session = AssistantSession(
                character=profile,
                provider=provider,
                runtime=runtime,
                pipeline=pipeline,
                mcp_provider=mcp_provider,
            )
            self._check_active(cancel)
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
