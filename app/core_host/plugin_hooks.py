"""按插件实例登记钩子，依次传递修改后的数据。"""

from __future__ import annotations

import json
import secrets
import threading
from dataclasses import dataclass
from time import monotonic

from app.core.diagnostics import exception_diagnostics
from app.core.runtime_log import log_event
from app.plugin_sdk.sakura_cancellation import check_cancelled
from app.plugin_sdk.sakura_hooks import HOOK_POINTS, HookError
from app.plugins.host_services import HOST_CALLER, HOST_CALLER_SCOPE, HOST_CALLER_LOG_METADATA


@dataclass
class _Hook:
    name: str
    owner: tuple[str, str]
    handle: str
    order: float
    failure_policy: str


class HookHostService:
    def __init__(self, invoke_callback, artifacts, commit_scope, *, usage_store=None):
        self._invoke_callback = invoke_callback
        self._artifacts = artifacts
        self._commit_scope = commit_scope
        self._lock = threading.RLock()
        self._registrations = {}
        self._usage_store = usage_store

    def call(self, method, args):
        if method == "describe" and not args:
            return {"points": dict(HOOK_POINTS), "failurePolicies": ["skip", "abort"]}
        if method == "catalog" and len(args) <= 1:
            with self._lock:
                return [{"registrationId": key, "hook": item.name, "pluginId": item.owner[0],
                         "order": item.order, "failurePolicy": item.failure_policy}
                        for key, item in self._registrations.items()
                        if not args or item.name == args[0]]
        if method == "register" and len(args) == 2:
            descriptor, handle = args
            name = descriptor.get("hook")
            order = descriptor.get("order", 100)
            policy = descriptor.get("failurePolicy", "skip")
            if (not isinstance(name, str) or not name.strip()
                    or not isinstance(order, (int, float)) or policy not in {"skip", "abort"}):
                raise HookError("HOOK_DESCRIPTOR_INVALID", "钩子名称、顺序或失败策略无效。")
            owner = (HOST_CALLER.get(), HOST_CALLER_SCOPE.get())
            registration = secrets.token_hex(16)

            def register():
                with self._lock:
                    self._registrations[registration] = _Hook(name, owner, handle, order, policy)
                return {"registrationId": registration}

            return self._commit_scope(*owner, register)
        if method == "unregister" and len(args) == 1:
            with self._lock:
                item = self._registrations.get(args[0])
                if item is not None and HOST_CALLER.get() not in {None, "sakura.core", item.owner[0]}:
                    raise HookError("HOOK_OWNER_INVALID", "不能撤销其他插件的钩子。")
                return {"removed": self._registrations.pop(args[0], None) is not None}
        if method == "run" and len(args) == 3:
            name, envelope, metadata = args
            owner = (HOST_CALLER.get(), HOST_CALLER_SCOPE.get())
            data = self._unpack(envelope, owner[0], release=False)

            def check():
                self._commit_scope(*owner, lambda: None)

            value = self.run(name, data, {**metadata, "callerId": owner[0], "callerName": HOST_CALLER_LOG_METADATA.get()[0]}, cancel_checker=check)
            return self._pack_for_scope(value, owner)
        raise HookError("HOST_METHOD_INVALID", "钩子服务方法无效。")

    def _pack(self, value, owner):
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(encoded) <= 32768:
            return {"value": value}
        return {"artifact": self._artifacts.create_json(owner, value)}

    def _unpack(self, envelope, owner, *, release=True):
        if "value" in envelope:
            return envelope["value"]
        artifact = self._artifacts.resolve_committed(envelope["artifact"]["artifactId"])
        if artifact.plugin_id != owner:
            raise HookError("HOOK_ARTIFACT_OWNER_INVALID", "钩子数据不属于当前插件。")
        try:
            return json.loads(artifact.path.read_bytes())
        finally:
            if release:
                self._artifacts.release_committed(artifact.artifact_id)

    def _pack_for_scope(self, value, owner):
        envelope = self._pack(value, owner[0])
        try:
            return self._commit_scope(*owner, lambda: envelope)
        except BaseException:
            if "artifact" in envelope:
                self._artifacts._release_owned(owner[0], envelope["artifact"]["artifactId"])
            raise

    def run(self, name, data, metadata=None, *, cancel_checker=None):
        check_cancelled(cancel_checker)
        if name == "model.after_response" and self._usage_store is not None:
            try:
                self._usage_store.record_response(data["response"], metadata)
            except Exception as error:
                log_event("Usage", "模型用量保存失败", exception_diagnostics(error,
                    reason_code="USAGE_WRITE_FAILED", stage="model.after_response"),
                    event="usage.write.failed", severity="warning")
        with self._lock:
            handlers = sorted(((key, item) for key, item in self._registrations.items() if item.name == name),
                              key=lambda row: row[1].order)
        current = data
        for registration, item in handlers:
            check_cancelled(cancel_checker)
            with self._lock:
                if self._registrations.get(registration) is not item:
                    continue
            started = monotonic()
            envelope = None
            try:
                envelope = self._pack_for_scope({
                    "hook": name, "data": current, "context": metadata or {},
                }, item.owner)
                result = self._unpack(self._invoke_callback(item.handle, "hooks.handler", envelope), item.owner[0])
                check_cancelled(cancel_checker)
                with self._lock:
                    if self._registrations.get(registration) is not item:
                        continue
                    if result is None:
                        continue
                    if result.get("abort") is True:
                        raise HookError("HOOK_ABORTED", str(result.get("reason") or "插件已中止当前操作。"),
                                        hook=name, plugin_id=item.owner[0])
                    current = {**current, **result.get("patch", {})}
            except Exception as error:
                check_cancelled(cancel_checker)
                with self._lock:
                    if self._registrations.get(registration) is not item:
                        continue
                if getattr(error, "code", "") == "HOOK_ABORTED" or item.failure_policy == "abort":
                    raise
                log_event("Plugin", "插件钩子执行失败，已跳过", {
                    **exception_diagnostics(error, reason_code=getattr(error, "code", "HOOK_FAILED"), stage=name), "hook": name,
                    "operation_id": (metadata or {}).get("operationId", ""),
                    "elapsed_ms": int((monotonic() - started) * 1000),
                }, event="plugin.hook.failed", plugin_id=item.owner[0], severity="warning")
            finally:
                if envelope is not None and "artifact" in envelope:
                    self._artifacts._release_owned(item.owner[0], envelope["artifact"]["artifactId"])
        check_cancelled(cancel_checker)
        return current

    def revoke_scope(self, plugin_id):
        with self._lock:
            self._registrations = {key: item for key, item in self._registrations.items()
                                   if item.owner[0] != plugin_id}

    def clear(self):
        with self._lock:
            self._registrations.clear()
