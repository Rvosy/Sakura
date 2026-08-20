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
    tool_actions: object | None = field(default=None, repr=False)
    mcp_provider: object | None = field(default=None, repr=False)
    plugin_worker: object | None = field(default=None, repr=False)

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
                plugins_enabled = self._plugins_enabled
            from app.core_host.tool_settings import load_tool_runtime_configuration

            runtime_loop_settings, confirm_writes = load_tool_runtime_configuration(
                self._app_root
            )
            if tools_enabled:
                from app.core_host.tools import create_runtime_v2_tool_registry

                tools = create_runtime_v2_tool_registry(confirm_writes=confirm_writes)
            else:
                from app.agent.tools import ToolRegistry

                tools = ToolRegistry([])
            owned.append(tools)
            mcp_provider: object | None = None
            if mcp_enabled:
                from app.agent.mcp.provider import start_mcp_tools_from_config
                from app.core.runtime_resources import ResourceRegistry
                from app.core_host.mcp_settings import load_mcp_runtime_settings

                mcp_resources = ResourceRegistry()
                mcp_provider = start_mcp_tools_from_config(
                    self._app_root,
                    tools,
                    runtime_settings=load_mcp_runtime_settings(self._app_root),
                    resource_registry=mcp_resources,
                )
                owned.append(mcp_provider)
            tool_actions: object | None = None
            if mcp_enabled or plugins_enabled:
                from app.core_host.tools import ToolActionCoordinator

                tool_actions = ToolActionCoordinator(
                    self._generation_id,
                    tool_lookup=tools.get,
                )
                owned.append(tool_actions)
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
            plugin_worker: object | None = None
            if plugins_enabled:
                from app.core_host.plugin_worker import PluginWorkerClient

                plugin_worker = PluginWorkerClient(self._app_root, self._generation_id)
                plugin_worker.configure_host_services(tools, runtime)
                plugin_worker.start()
                owned.append(plugin_worker)
                plugin_worker.bind_runtime(tools, runtime)
            self._check_active(cancel)

            pipeline = ChatPipeline(runtime, finalize_trace_operations=False)
            owned.append(pipeline)
            self._check_active(cancel)

            session = AssistantSession(
                character=profile,
                provider=provider,
                runtime=runtime,
                pipeline=pipeline,
                tool_actions=tool_actions,
                mcp_provider=mcp_provider,
                plugin_worker=plugin_worker,
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

    def _check_active(self, cancel: Event) -> None:
        if cancel.is_set():
            raise OperationCancelled()
        with self._lock:
            if self._closed:
                raise OperationCancelled()
