from __future__ import annotations

import sys
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Literal
from types import SimpleNamespace
from app.plugin_sdk.sakura_assistant_contract import ChatReply, ChatSegment

from app.config.app_version import read_app_version
from app.config.character_loader import (
    CharacterProfile,
    CharacterRegistry,
    load_character_system_prompt,
)
from app.config.core_config_reader import CoreConfigReader
from app.plugin_sdk.sakura_cancellation import OperationCancelled
from app.core.diagnostics import diagnostic_secret_scope, register_diagnostic_secret
from app.core.runtime_log import diagnostic_attributes, log_event
from app.core_host.character_presentation import project_character_presentation
from app.storage.runtime_roots import coerce_runtime_roots


@dataclass
class AssistantSession:
    character: CharacterProfile
    assistant: "BoundAssistant"
    loop_settings: object
    app_version: str
    visual_binding: object | None = None
    model_slots: dict = field(default_factory=dict, repr=False)

    def descriptor(self):
        return {"character": {"id": self.character.id, "displayName": self.character.display_name,
                "replyTones": list(self.character.reply_tones), "systemPrompt": load_character_system_prompt(self.character)},
                "loopSettings": asdict(self.loop_settings), "appVersion": self.app_version, "modelSlots": self.model_slots,
                "replyVisual": self.visual_binding.reply_visual if self.visual_binding is not None else None}


class AssistantFailure(RuntimeError):
    def __init__(self, failure):
        super().__init__(failure["code"])
        self.code = failure["code"]
        self.public_message = failure["message"]
        self.retryable = bool(failure["retryable"])
        self.log_attributes = dict(failure.get("attributes", {}))


class BoundAssistant:
    def __init__(self, application, identity):
        self.application = application
        self.identity = dict(identity)

    def call(self, method, *args):
        return self.application.call_bound_service("sakura.assistant", self.identity, method, *args)

    def prepare(self, descriptor):
        return self.call("prepare", descriptor)

    def commit_result(self, commit):
        from app.plugins.runtime_v4 import PluginRuntimeError

        try:
            return self.application.commit_bound_service("sakura.assistant", self.identity, commit)
        except PluginRuntimeError as error:
            raise AssistantFailure({"code": "ASSISTANT_BINDING_EXPIRED",
                "message": "Assistant 已停用或重新加载，请重新发送。", "retryable": True}) from error

    def run_turn(self, descriptor, *, cancel_checker, progress_callback=None):
        operation_id = descriptor["operationId"]
        artifact = self.application.create_assistant_input(self.identity, descriptor)
        attempted = False
        started = False
        stopped = False
        sequence = 0
        try:
            cancel_checker()
            attempted = True
            try:
                self.call("begin", {"operationId": operation_id, "input": artifact})
            except Exception as error:
                if getattr(error, "code", "") in {"ASSISTANT_BUSY", "ASSISTANT_CLOSED", "ASSISTANT_INPUT_INVALID"}:
                    attempted = False
                raise
            started = True
            while True:
                cancel_checker()
                state = self.call("poll", operation_id, sequence, 500)
                sequence = state["sequence"]
                if progress_callback is not None:
                    for progress in state["progress"]:
                        progress_callback(progress)
                if state["state"] != "running":
                    stopped = True
                    break
            cancel_checker()
            try:
                result_artifact = self.call("result", operation_id)
            except Exception as error:
                failure = state.get("failure")
                if failure:
                    raise AssistantFailure(failure) from error
                raise
            result = self.application.read_assistant_result(result_artifact)
            try:
                segments = result["reply"]["segments"]
                actions = result["actions"]
                if not isinstance(segments, list) or not isinstance(actions, list):
                    raise ValueError("reply collections must be arrays")
                if any(not isinstance(item, dict) or not isinstance(item.get("text"), str) for item in segments):
                    raise ValueError("reply text must be a string")
                return SimpleNamespace(reply=ChatReply([ChatSegment(**item) for item in segments]),
                    actions=[SimpleNamespace(**item) for item in actions], visual_observation=result.get("visual_observation"))
            except (KeyError, TypeError, ValueError) as error:
                raise AssistantFailure({"code": "INVALID_CHAT_REPLY", "message": "Assistant 回复格式无效。", "retryable": False}) from error
        except BaseException:
            if attempted and not stopped:
                if started:
                    try:
                        self.call("cancel", operation_id)
                        # Keep admission and history grants until the worker exits.
                        # A tool already executing is never sent a second time.
                        while self.call("poll", operation_id, sequence, 500)["state"] == "running":
                            pass
                        stopped = True
                    except Exception as error:
                        report_assistant_failure(error, stage="cancel", code="ASSISTANT_CLEANUP_FAILED")
                if not stopped:
                    try:
                        # A missing begin acknowledgement cannot establish whether
                        # a worker exists. End that exact process lifetime.
                        self.application.abort_bound_service("sakura.assistant", self.identity,
                            reason="ASSISTANT_CALL_UNCERTAIN")
                        stopped = True
                    except Exception as error:
                        report_assistant_failure(error, stage="abort", code="ASSISTANT_CLEANUP_FAILED")
            raise
        finally:
            if not attempted or stopped:
                try:
                    self.application.release_assistant_input(artifact["artifactId"])
                except Exception as error:
                    report_assistant_failure(error, stage="input_release", code="ASSISTANT_CLEANUP_FAILED")

    def release(self, operation_id, *, status):
        try:
            result = self.call("release", operation_id, status)
            if not result["released"]:
                # release asks the worker to dispose itself on exit.
                while True:
                    try:
                        self.call("poll", operation_id, 0, 500)
                    except Exception as error:
                        if getattr(error, "code", "") == "ASSISTANT_OPERATION_NOT_FOUND":
                            break
                        raise
            return {"released": True}
        except Exception as error:
            report_assistant_failure(error, stage="release", code="ASSISTANT_CLEANUP_FAILED")
            try:
                self.application.abort_bound_service("sakura.assistant", self.identity,
                    reason="ASSISTANT_CALL_UNCERTAIN")
            except Exception as cleanup_error:
                report_assistant_failure(cleanup_error, stage="abort", code="ASSISTANT_CLEANUP_FAILED")
            raise


def apply_visual_reply(reply, binding):
    if not isinstance(getattr(reply, "segments", None), list):
        raise AssistantFailure({"code": "INVALID_CHAT_REPLY", "message": "Assistant 回复格式无效。", "retryable": False})
    segments = []
    for segment in reply.segments:
        control = None
        if binding is not None:
            parsed = binding.parse_control(segment.control, legacy={"portrait": segment.portrait, "tone": segment.tone}, segment={"tone": segment.tone})
            control = parsed.control
            if parsed.reason_code not in {"READY", "VISUAL_BINDING_EXPIRED"}:
                log_event("Visual", "表现控制未应用", diagnostic_attributes(parsed.error or RuntimeError(parsed.reason_code),
                    reason_code=parsed.reason_code, stage="visual.parse_control"), event="visual.control.failed", severity="warning")
        segments.append(replace(segment, control=control))
    return ChatReply(segments)


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


def report_assistant_failure(error: Exception, *, stage: str, code: str) -> None:
    """Record the original failure where it still has its traceback and cause."""
    cleanup = stage in {"close", "cancel", "abort", "input_release", "release"}
    try:
        log_event(
            "Assistant",
            "Assistant 资源关闭失败" if cleanup else "Assistant 初始化失败",
            diagnostic_attributes(error, reason_code=code, stage=stage),
            event="assistant.resource.close_failed" if cleanup else "assistant.initialization.failed",
            severity="error",
            verbosity=0,
        )
    except Exception:
        # A broken diagnostic sink must not replace the operation's failure.
        try:
            print(f"Assistant {stage} failed: {type(error).__name__}", file=sys.stderr)
        except Exception:
            pass


class AssistantAdapter:
    def __init__(self, roots, *, config_reader=None):
        self._roots = coerce_runtime_roots(roots)
        self._config_reader = config_reader or CoreConfigReader()
        self._closed = False
        self._application = None

    def bind_application(self, application):
        self._application = application

    @diagnostic_secret_scope()
    def initialize(self, cancel):
        stage = "configuration"
        try:
            if self._closed or cancel.is_set():
                raise OperationCancelled()
            config = self._config_reader.read(self._roots.user_root)
            if config.provider_selection is not None:
                register_diagnostic_secret(config.provider_selection.api_settings.api_key)
            problem = config.config_problem
            # Model readiness belongs to the selected Assistant provider.
            if problem is not None and problem.code != "PROVIDER_SETUP_REQUIRED":
                return ReadinessResult(problem.state, problem.code, problem.message, problem.retryable, None)
            registry = CharacterRegistry(self._roots.user_root, issue_sink=_safe_character_issue_sink)
            profile = registry.profiles.get(config.current_character_id)
            if profile is None:
                return ReadinessResult("setup_required", "CHARACTER_REQUIRED", "请安装并选择角色。", False, None)
            stage = "assistant_service"
            if self._application is None:
                return ReadinessResult("setup_required", "ASSISTANT_PROVIDER_REQUIRED", "请启用 Assistant 插件。", False, None)
            from app.plugins.runtime_v4 import PluginRuntimeError
            try:
                identity = self._application.service_identity("sakura.assistant")
            except PluginRuntimeError as error:
                if error.code != "SERVICE_MISSING":
                    raise
                return ReadinessResult("setup_required", "ASSISTANT_PROVIDER_REQUIRED", "请启用 Assistant 插件。", False,
                    None, project_character_presentation(profile))
            assistant = BoundAssistant(self._application, identity)
            from app.core_host.tool_settings import load_tool_runtime_configuration
            session = AssistantSession(profile, assistant, load_tool_runtime_configuration(self._roots.user_root),
                read_app_version(self._roots.distribution_root), model_slots=self._application.active_models())
            ready = assistant.prepare(session.descriptor())
            if self._closed or cancel.is_set():
                raise OperationCancelled()
            active = ready["state"] in {"ready", "degraded"}
            return ReadinessResult(ready["state"], ready["code"], ready["message"], bool(ready["retryable"]),
                project_current_character_summary(profile) if active else None,
                project_character_presentation(profile), session if active else None)
        except OperationCancelled:
            raise
        except Exception as error:
            report_assistant_failure(error, stage=stage, code="ASSISTANT_INITIALIZATION_FAILED")
            return ReadinessResult("failed", "ASSISTANT_INITIALIZATION_FAILED", "Assistant 初始化失败。", False, None)

    def close(self):
        self._closed = True

    def retire_session(self):
        # The application owns the provider process. Retiring UI readiness cannot resurrect a local Agent.
        return None
