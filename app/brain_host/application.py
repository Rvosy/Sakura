"""无 Qt Brain Host 应用装配与系统方法。"""

from __future__ import annotations

import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.brain_host.dto import startup_state_dto
from app.brain_host.errors import BrainHostError
from app.brain_host.protocol import PROTOCOL_VERSION


ContextBuilder = Callable[[Path], Any]


@dataclass(frozen=True)
class BrainHostConfig:
    base_dir: Path
    session_id: str
    session_credential: str
    protocol_version: int = PROTOCOL_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_dir", Path(self.base_dir).resolve())
        if not self.base_dir.is_dir():
            raise BrainHostError("INVALID_STARTUP_CONFIG", "base_dir must be an existing directory")
        if not self.session_id.strip():
            raise BrainHostError("INVALID_STARTUP_CONFIG", "session_id is required")
        if not self.session_credential.strip():
            raise BrainHostError("INVALID_STARTUP_CONFIG", "session credential is required")
        if self.protocol_version != PROTOCOL_VERSION:
            raise BrainHostError("PROTOCOL_VERSION_UNSUPPORTED", "unsupported protocol version")

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> "BrainHostConfig":
        base_dir = environment.get("SAKURA_BASE_DIR", "").strip()
        session_id = environment.get("SAKURA_SESSION_ID", "").strip()
        credential = environment.get("SAKURA_SESSION_CREDENTIAL", "").strip()
        protocol_text = environment.get("SAKURA_PROTOCOL_VERSION", str(PROTOCOL_VERSION)).strip()
        try:
            protocol_version = int(protocol_text)
        except ValueError as exc:
            raise BrainHostError(
                "INVALID_STARTUP_CONFIG",
                "protocol version must be an integer",
            ) from exc
        if not base_dir:
            raise BrainHostError("INVALID_STARTUP_CONFIG", "base_dir is required")
        return cls(Path(base_dir), session_id, credential, protocol_version)


class BrainHostApplication:
    def __init__(
        self,
        config: BrainHostConfig,
        *,
        context_builder: ContextBuilder | None = None,
    ) -> None:
        self.config = config
        self._context_builder = context_builder or _build_context
        self.context: Any | None = None
        self.assistant: Any | None = None
        self.scheduler: Any | None = None
        self.state = "starting"
        self.startup: dict[str, Any] | None = None
        self.initialization_error: BrainHostError | None = None

    def initialize(self) -> dict[str, Any] | None:
        if self.state == "ready":
            return self.startup
        try:
            self.context = self._context_builder(self.config.base_dir)
            self.startup = startup_state_dto(self.context)
            if hasattr(self.context, "agent_runtime"):
                from app.brain_host.scheduler import PeriodicScheduler
                from app.core.assistant_service import AssistantApplication
                from app.core.chat_pipeline import ChatPipeline

                self.assistant = AssistantApplication(
                    ChatPipeline(
                        self.context.agent_runtime,
                        visual_observation_store=getattr(
                            self.context,
                            "visual_observation_store",
                            None,
                        ),
                    ),
                    session_id=self.config.session_id,
                )
                self.scheduler = PeriodicScheduler()
        except Exception as exc:  # noqa: BLE001
            self.state = "failed"
            self.initialization_error = BrainHostError(
                "BACKEND_INITIALIZATION_FAILED",
                "Brain Host initialization failed",
                retryable=True,
                details={"error_type": type(exc).__name__},
            )
            return None
        self.state = "ready"
        return self.startup

    def handle_request(self, method: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        if method == "system.hello":
            return self._hello(payload)
        if method == "system.health":
            return self._health()
        if method == "system.shutdown":
            return self._shutdown()
        raise BrainHostError("METHOD_NOT_FOUND", f"Unknown Brain Host method: {method}")

    def _hello(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if payload.get("protocol") != self.config.protocol_version:
            raise BrainHostError("PROTOCOL_VERSION_UNSUPPORTED", "protocol version mismatch")
        credential = payload.get("session_credential")
        if not isinstance(credential, str) or not secrets.compare_digest(
            credential,
            self.config.session_credential,
        ):
            raise BrainHostError("AUTHENTICATION_FAILED", "session credential is invalid")
        return {
            "protocol": self.config.protocol_version,
            "session_id": self.config.session_id,
            "backend_state": self.state,
            "startup": self.startup,
        }

    def _health(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "state": self.state,
            "ready": self.state == "ready",
        }
        if self.context is not None:
            result["character_id"] = self.context.character_profile.id
        if self.initialization_error is not None:
            result["error"] = self.initialization_error.to_dict()
        return result

    def _shutdown(self) -> dict[str, Any]:
        if self.state == "stopped":
            return {"state": "stopped"}
        self.state = "stopping"
        context = self.context
        if self.scheduler is not None:
            self.scheduler.stop(timeout=1)
        if self.assistant is not None:
            self.assistant.close(wait=True)
        if context is not None:
            _close_quietly(getattr(context, "mcp_tool_provider", None), "close")
            _close_quietly(getattr(context, "plugin_manager", None), "shutdown_all")
            _close_quietly(getattr(context, "tts_provider", None), "close")
            registry = getattr(context, "resource_registry", None)
            if registry is not None:
                registry.stop_all(1_000)
        self.state = "stopped"
        return {"state": "stopped"}


def _build_context(base_dir: Path) -> Any:
    from app.core.bootstrap import build_initial_app_context

    return build_initial_app_context(base_dir)


def _close_quietly(target: object | None, method: str) -> None:
    callback = getattr(target, method, None)
    if callable(callback):
        try:
            callback()
        except Exception:  # noqa: BLE001
            pass
