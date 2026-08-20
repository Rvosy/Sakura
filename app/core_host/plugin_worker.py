"""Generation-private client for the Runtime v2 Python plugin worker."""

from __future__ import annotations

import json
import os
import secrets
import struct
import subprocess
import sys
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, BinaryIO, Mapping

from app.core.process_tree import terminate_process_tree
from app.llm.prompts.types import ContextRequest
from app.plugins.models import ContextProviderContribution
from app.plugins.inventory import PluginDesiredStateStore, PluginInventory


MAX_PRIVATE_FRAME_BYTES = 1024 * 1024
MAX_PENDING_REQUESTS = 16
DEFAULT_CALL_TIMEOUT_SECONDS = 3.0
INITIALIZE_TIMEOUT_SECONDS = 8.0
CLOSE_TIMEOUT_SECONDS = 0.8
FORCE_TERMINATE_TIMEOUT_SECONDS = 0.5


class PluginWorkerError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class _Pending:
    def __init__(self) -> None:
        self.done = threading.Event()
        self.payload: object = None
        self.error: PluginWorkerError | None = None


class PluginWorkerClient:
    """Own one subprocess and bounded request map for a Core generation."""

    def __init__(
        self,
        app_root: Path,
        generation_id: str,
        *,
        call_timeout: float = DEFAULT_CALL_TIMEOUT_SECONDS,
    ) -> None:
        if not isinstance(generation_id, str) or not generation_id.strip():
            raise ValueError("plugin generation identity must not be empty")
        self._app_root = Path(app_root).resolve()
        self._generation_id = generation_id
        self._token = secrets.token_hex(16)
        self._call_timeout = max(0.05, float(call_timeout))
        self._process: subprocess.Popen[bytes] | None = None
        self._reader: threading.Thread | None = None
        self._initializer: threading.Thread | None = None
        self._binders: list[threading.Thread] = []
        self._tool_registry: object | None = None
        self._runtime: object | None = None
        self._host_services: object | None = None
        self._desired_state = PluginDesiredStateStore(self._app_root)
        self._writer_lock = threading.Lock()
        self._restart_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._pending_slots = threading.BoundedSemaphore(MAX_PENDING_REQUESTS)
        self._pending: dict[str, _Pending] = {}
        self._snapshot: dict[str, Any] | None = None
        self._state = "stopped"
        self._reason_code = "WORKER_STOPPED"
        self._closed = False
        self._quiescing = False
        self._load_done = threading.Event()
        self._bind_done = threading.Event()
        self._bound = False
        self._session_binding: tuple[str, str] | None = None
        self._binding_epoch = 0
        self._last_lifecycle_recovered = False

    @property
    def state(self) -> str:
        with self._state_lock:
            return self._state

    @property
    def reason_code(self) -> str:
        with self._state_lock:
            return self._reason_code

    @property
    def last_lifecycle_recovered(self) -> bool:
        with self._state_lock:
            return self._last_lifecycle_recovered

    def start(self) -> None:
        with self._state_lock:
            if self._closed or self._quiescing:
                raise PluginWorkerError("GENERATION_INVALIDATED", "插件 generation 已失效。")
            if self._process is not None:
                return
            self._spawn_worker_locked()

    def _spawn_worker_locked(self) -> None:
        self._token = secrets.token_hex(16)
        self._snapshot = None
        self._state = "starting"
        self._reason_code = "WORKER_STARTING"
        self._load_done = threading.Event()
        self._bind_done = threading.Event()
        self._bound = False
        environment = os.environ.copy()
        project_root = str(Path(__file__).resolve().parents[2])
        python_path = environment.get("PYTHONPATH", "")
        environment["PYTHONPATH"] = os.pathsep.join(
            item for item in (project_root, python_path) if item
        )
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "app.core_host.plugin_worker_runtime",
                "--app-root",
                str(self._app_root),
                "--generation-id",
                self._generation_id,
                "--token",
                self._token,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=project_root,
            env=environment,
            bufsize=0,
        )
        self._process = process
        token = self._token
        load_done = self._load_done
        self._reader = threading.Thread(
            target=self._read_loop,
            args=(process, token, load_done),
            name="sakura-plugin-worker-reader",
            daemon=True,
        )
        self._reader.start()
        self._initializer = threading.Thread(
            target=self._initialize,
            args=(process, token, load_done),
            name="sakura-plugin-worker-initialize",
            daemon=True,
        )
        self._initializer.start()

    def wait_until_loaded(self, *, timeout: float = INITIALIZE_TIMEOUT_SECONDS) -> dict[str, Any]:
        if not self._load_done.wait(max(0.0, timeout)):
            raise PluginWorkerError("PLUGIN_INITIALIZE_TIMEOUT", "插件加载超时。", retryable=True)
        with self._state_lock:
            if self._snapshot is None:
                raise PluginWorkerError(self._reason_code, "插件 worker 未能完成加载。", retryable=True)
            return _clone_mapping(self._snapshot)

    def public_snapshot(self) -> dict[str, Any]:
        with self._state_lock:
            if self._snapshot is not None:
                result = _clone_mapping(self._snapshot)
                result["state"] = self._state
                result["reasonCode"] = self._reason_code
                return result
            return {
                "schemaVersion": 1,
                "state": self._state,
                "reasonCode": self._reason_code,
                "plugins": [],
            }

    def call_service(self, service_key: str, method: str, *args: object) -> object:
        """Call one explicitly exported method on a Worker-local v3 Service."""
        return self._request_with_recovery(
            "service.call",
            {"serviceKey": service_key, "method": method, "args": list(args)},
            recovery="sync_raise",
        )

    def invoke_callback(self, handle: str, shape: str, *args: object) -> object:
        """Invoke a generation-bound Worker callback previously registered with Host."""
        return self._request_with_recovery(
            "callback.invoke",
            {"handle": handle, "shape": shape, "args": list(args)},
            recovery="async_raise",
        )

    def transform(self, hook: str, value: object) -> object:
        """Run a generic v3 transform inside the private Worker."""
        return self._request_with_recovery(
            "hook.transform",
            {"hook": hook, "value": value},
            recovery="async_raise",
        )

    def set_plugin_enabled(self, plugin_id: str, enabled: bool) -> dict[str, Any]:
        """Reconcile only this Worker's in-memory lifecycle state."""
        result = self._lifecycle_request(
            "lifecycle.set_enabled",
            {"pluginId": plugin_id, "enabled": enabled},
        )
        if not isinstance(result, Mapping):
            raise PluginWorkerError("PLUGIN_RESPONSE_INVALID", "插件状态响应无效。")
        snapshot = dict(result)
        with self._state_lock:
            if not self._closed:
                self._snapshot = snapshot
                self._state = str(snapshot.get("state", "ready"))
                self._reason_code = str(snapshot.get("reasonCode", "READY"))
        return _clone_mapping(snapshot)

    def reload_plugin(self, plugin_id: str) -> dict[str, Any]:
        """Reload one v3 plugin and its required consumers in the same Worker."""
        result = self._lifecycle_request("lifecycle.reload", {"pluginId": plugin_id})
        if not isinstance(result, Mapping):
            raise PluginWorkerError("PLUGIN_RESPONSE_INVALID", "插件状态响应无效。")
        snapshot = dict(result)
        with self._state_lock:
            if not self._closed:
                self._snapshot = snapshot
                self._state = str(snapshot.get("state", "ready"))
                self._reason_code = str(snapshot.get("reasonCode", "READY"))
        return _clone_mapping(snapshot)

    def rebuild(self) -> dict[str, Any]:
        """Rescan installed code by replacing only this generation's Plugin Worker."""

        with self._state_lock:
            token = self._token
        return self._rebuild_worker(token, graceful=True)

    def refresh_status(self) -> dict[str, Any]:
        result = self._request_with_recovery(
            "status.get",
            {},
            recovery="sync_return",
        )
        if not isinstance(result, Mapping):
            raise PluginWorkerError("PLUGIN_RESPONSE_INVALID", "插件状态响应无效。")
        snapshot = dict(result)
        with self._state_lock:
            if not self._closed:
                self._snapshot = snapshot
                self._state = str(snapshot.get("state", "ready"))
                self._reason_code = str(snapshot.get("reasonCode", "READY"))
        return _clone_mapping(snapshot)

    def configure_host_services(self, tool_registry: object, runtime: object) -> None:
        """Install Core-owned Host Services before the Worker starts loading plugins."""
        from app.core_host.plugin_host_services import PluginHostServices
        from app.core_host.plugin_artifacts import PluginArtifactStore
        from app.core_host.plugin_character import PluginCharacterStore

        with self._state_lock:
            if self._closed:
                raise PluginWorkerError("GENERATION_INVALIDATED", "插件 generation 已失效。")
            if self._host_services is not None:
                return
            self._tool_registry = tool_registry
            self._runtime = runtime
            self._host_services = PluginHostServices(
                tool_registry,
                artifact_store=PluginArtifactStore(self._app_root, self._generation_id),
                character_store=PluginCharacterStore(self._app_root),
                invoke_callback=self.invoke_callback,
                encode_context_request=_context_request_mapping,
                on_context_change=self._host_context_changed,
                reload_plugin=self.reload_plugin,
            )

    def bind_runtime(self, tool_registry: object, runtime: object) -> None:
        """Finish generic Host event binding without delaying Assistant readiness."""

        if self._host_services is None:
            # Compatibility for old call sites.  Production configures before start so
            # v3 manifests can see Host Services during dependency resolution.
            self.configure_host_services(tool_registry, runtime)
        with self._state_lock:
            binding_epoch = self._binding_epoch
            bind_done = self._bind_done

        def bind() -> None:
            try:
                self.wait_until_loaded()
                if self.state not in {"ready", "degraded"}:
                    return
                with self._state_lock:
                    if self._closed or self._binding_epoch != binding_epoch:
                        return
                    self._tool_registry = tool_registry
                    self._runtime = runtime
                getattr(tool_registry, "set_event_emitter")(
                    lambda event_name, payload: self.emit_event(event_name, payload or {})
                )
                with self._state_lock:
                    if self._binding_epoch != binding_epoch:
                        return
                    session_binding = self._session_binding
                if session_binding is not None:
                    self._request(
                        "session.bind",
                        {
                            "sessionId": session_binding[0],
                            "characterId": session_binding[1],
                        },
                    )
                with self._state_lock:
                    if not self._closed and self._binding_epoch == binding_epoch:
                        self._bound = True
            except (PluginWorkerError, AttributeError, TypeError, ValueError):
                return
            finally:
                bind_done.set()

        thread = threading.Thread(target=bind, name="sakura-plugin-worker-bind", daemon=True)
        with self._state_lock:
            if self._closed:
                return
            self._binders.append(thread)
        thread.start()

    def bind_session(
        self,
        session_id: str,
        character_id: str,
        tool_registry: object,
        runtime: object,
    ) -> None:
        with self._state_lock:
            if self._closed:
                raise PluginWorkerError("GENERATION_INVALIDATED", "插件 generation 已失效。")
            self._session_binding = (session_id, character_id)
            self._tool_registry = tool_registry
            self._runtime = runtime
            self._binding_epoch += 1
            self._bind_done = threading.Event()
        self.bind_runtime(tool_registry, runtime)

    def unbind_session(self) -> None:
        with self._state_lock:
            binding = self._session_binding
            self._session_binding = None
            self._binding_epoch += 1
            registry = self._tool_registry
            runtime = self._runtime
            self._runtime = None
            self._bound = False
            self._bind_done = threading.Event()
            self._bind_done.set()
        if binding is not None and self._process is not None:
            try:
                self._request("session.unbind", {}, timeout=CLOSE_TIMEOUT_SECONDS)
            except PluginWorkerError:
                pass
        if registry is not None:
            try:
                getattr(registry, "set_event_emitter")(None)
            except (AttributeError, TypeError):
                pass
        if runtime is not None:
            try:
                getattr(runtime, "set_context_providers")([])
            except (AttributeError, TypeError):
                pass

    def wait_until_bound(self, *, timeout: float = INITIALIZE_TIMEOUT_SECONDS) -> bool:
        """Wait until asynchronous contribution binding has settled."""

        if not self._bind_done.wait(max(0.0, timeout)):
            return False
        with self._state_lock:
            return not self._closed and self._bound

    def emit_event(self, event_type: str, payload: Mapping[str, Any]) -> None:
        self._request_with_recovery(
            "event.emit",
            {"eventType": event_type, "payload": dict(payload)},
            recovery="async_raise",
        )

    def settings_snapshot(self) -> dict[str, Any]:
        result = self.refresh_status()
        with self._state_lock:
            host_services = self._host_services
        if host_services is None:
            return dict(result)
        try:
            decorated = getattr(host_services, "decorate_settings_snapshot")(result)
        except Exception as error:
            code = str(getattr(error, "code", "PLUGIN_RESPONSE_INVALID"))
            raise PluginWorkerError(code, "插件设置响应无效。") from error
        if not isinstance(decorated, Mapping):
            raise PluginWorkerError("PLUGIN_RESPONSE_INVALID", "插件设置响应无效。")
        return dict(decorated)

    def settings_save(self, plugin_id: str, section_id: str, values: Mapping[str, Any]) -> object:
        with self._state_lock:
            host_services = self._host_services
        if host_services is not None:
            try:
                handled, result = getattr(host_services, "settings_save")(
                    plugin_id,
                    section_id,
                    values,
                )
            except Exception as error:
                code = str(getattr(error, "code", "SETTINGS_SAVE_FAILED"))
                raise PluginWorkerError(code, "插件设置保存失败。") from error
            if handled:
                return result
        raise PluginWorkerError(
            "SETTINGS_ID_INVALID",
            "插件设置区块不存在。",
        )

    def settings_sections(self, surface: str) -> list[dict[str, Any]]:
        with self._state_lock:
            host_services = self._host_services
        if host_services is None:
            return []
        try:
            result = getattr(host_services, "settings_sections")(surface)
        except Exception as error:
            code = str(getattr(error, "code", "SETTINGS_SURFACE_INVALID"))
            raise PluginWorkerError(code, "插件设置表面不可用。") from error
        return [dict(item) for item in result if isinstance(item, Mapping)]

    def model_slots(self) -> list[dict[str, Any]]:
        with self._state_lock:
            host_services = self._host_services
        if host_services is None:
            return []
        try:
            result = getattr(host_services, "model_slots")()
        except Exception as error:
            code = str(getattr(error, "code", "MODEL_SLOTS_UNAVAILABLE"))
            raise PluginWorkerError(code, "插件模型槽位不可用。") from error
        return [dict(item) for item in result if isinstance(item, Mapping)]

    def model_slot_save(self, identity: str, selection: Mapping[str, Any]) -> object:
        with self._state_lock:
            host_services = self._host_services
        if host_services is None:
            raise PluginWorkerError("MODEL_SLOT_UNAVAILABLE", "插件模型槽位不可用。")
        try:
            return getattr(host_services, "model_slot_save")(identity, selection)
        except Exception as error:
            code = str(getattr(error, "code", "MODEL_SLOT_SAVE_FAILED"))
            raise PluginWorkerError(code, "插件模型槽位保存失败。") from error

    def settings_action(
        self,
        plugin_id: str,
        section_id: str,
        action_id: str,
        values: Mapping[str, Any],
    ) -> object:
        with self._state_lock:
            host_services = self._host_services
        if host_services is not None:
            try:
                handled, result = getattr(host_services, "settings_action")(
                    plugin_id,
                    section_id,
                    action_id,
                    values,
                )
            except Exception as error:
                code = str(getattr(error, "code", "SETTINGS_ACTION_FAILED"))
                raise PluginWorkerError(code, "插件设置动作失败。") from error
            if handled:
                return result
        raise PluginWorkerError(
            "SETTINGS_ACTION_INVALID",
            "插件设置动作不存在。",
        )

    def settings_collection(
        self,
        operation: str,
        plugin_id: str,
        section_id: str,
        collection_id: str,
        payload: Mapping[str, Any],
    ) -> object:
        with self._state_lock:
            host_services = self._host_services
        if host_services is None:
            raise PluginWorkerError(
                "SETTINGS_COLLECTION_UNAVAILABLE",
                "插件 Collection 不可用。",
            )
        try:
            return getattr(host_services, "settings_collection")(
                operation,
                plugin_id,
                section_id,
                collection_id,
                payload,
            )
        except Exception as error:
            code = str(getattr(error, "code", "SETTINGS_COLLECTION_FAILED"))
            raise PluginWorkerError(code, "插件 Collection 操作失败。") from error

    def resolve_committed_artifact(self, artifact_id: str) -> object:
        """Resolve a committed Worker artifact without exposing its path over Bridge."""

        with self._state_lock:
            host_services = self._host_services
            if self._closed or host_services is None:
                raise PluginWorkerError("GENERATION_INVALIDATED", "插件 generation 已失效。")
        try:
            return getattr(host_services, "resolve_committed_artifact")(artifact_id)
        except Exception as error:
            code = str(getattr(error, "code", "ARTIFACT_NOT_FOUND"))
            raise PluginWorkerError(code, "插件 artifact 不可用。") from error

    def release_committed_artifact(self, artifact_id: str) -> bool:
        with self._state_lock:
            host_services = self._host_services
            if self._closed or host_services is None:
                return False
        try:
            return bool(getattr(host_services, "release_committed_artifact")(artifact_id))
        except Exception:
            return False

    def quiesce(self) -> None:
        """Prevent timeout recovery from spawning a new Worker during teardown."""

        with self._state_lock:
            self._quiescing = True

    def close(self) -> None:
        deadline = time.monotonic() + CLOSE_TIMEOUT_SECONDS
        with self._state_lock:
            if self._closed:
                return
            self._quiescing = True
            self._closed = True
            process = self._process
            if process is None:
                self._state = "stopped"
                self._reason_code = "WORKER_STOPPED"
                self._load_done.set()
                return
            self._state = "stopping"
            self._reason_code = "WORKER_STOPPING"
            self._invalidate_contributions_locked()
        try:
            if process.poll() is None:
                try:
                    self._request(
                        "worker.close",
                        {},
                        timeout=max(0.05, deadline - time.monotonic()),
                        allow_closed=True,
                        terminate_on_timeout=False,
                    )
                except PluginWorkerError:
                    pass
                try:
                    process.wait(timeout=max(0.0, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    terminate_process_tree(
                        process,
                        timeout=FORCE_TERMINATE_TIMEOUT_SECONDS,
                    )
        finally:
            for stream in (process.stdin, process.stdout):
                try:
                    if stream is not None:
                        stream.close()
                except OSError:
                    pass
            reader = self._reader
            if reader is not None and reader is not threading.current_thread():
                reader.join(timeout=max(0.0, deadline - time.monotonic()))
            for thread in [self._initializer, *self._binders]:
                if thread is not None and thread is not threading.current_thread():
                    thread.join(timeout=max(0.0, deadline - time.monotonic()))
            with self._state_lock:
                self._process = None
                self._snapshot = None
                self._state = "stopped"
                self._reason_code = "WORKER_STOPPED"
                self._invalidate_contributions_locked()
                self._host_services = None
                self._fail_pending_locked("GENERATION_INVALIDATED")
                self._load_done.set()

    def _initialize(
        self,
        process: subprocess.Popen[bytes],
        token: str,
        load_done: threading.Event,
    ) -> None:
        try:
            with self._state_lock:
                host_services = self._host_services
                available_host_services = list(
                    getattr(host_services, "available_keys", ())
                )
            inventory = PluginInventory(
                self._app_root,
                self._desired_state,
            ).scan()
            payload = self._request(
                "worker.initialize",
                {
                    "hostServices": available_host_services,
                    "runtimePlugins": [
                        spec.private_dict() for spec in inventory.runtime_specs
                    ],
                },
                timeout=INITIALIZE_TIMEOUT_SECONDS,
            )
            if not isinstance(payload, Mapping):
                raise PluginWorkerError("PLUGIN_RESPONSE_INVALID", "插件加载响应无效。")
            snapshot = dict(payload)
            with self._state_lock:
                if self._closed or self._process is not process or self._token != token:
                    return
                self._snapshot = snapshot
                self._state = str(snapshot.get("state", "ready"))
                self._reason_code = str(snapshot.get("reasonCode", "READY"))
        except PluginWorkerError as error:
            with self._state_lock:
                if not self._closed and self._process is process and self._token == token:
                    self._state = "degraded"
                    self._reason_code = error.code
        finally:
            load_done.set()

    def _lifecycle_request(self, name: str, payload: Mapping[str, Any]) -> object:
        with self._state_lock:
            failed_token = self._token
            self._last_lifecycle_recovered = False
        try:
            return self._request(name, payload)
        except PluginWorkerError as error:
            if error.code != "PLUGIN_CALL_TIMEOUT":
                raise
            result = self._restart_after_timeout(failed_token)
            with self._state_lock:
                self._last_lifecycle_recovered = True
            return result

    def _request_with_recovery(
        self,
        name: str,
        payload: Mapping[str, Any],
        *,
        recovery: str,
    ) -> object:
        """Apply one of the three stable timeout recovery policies.

        Original side-effecting calls are never replayed.  ``sync_return`` is
        reserved for read-only status, where the rebuilt snapshot is the result.
        """

        with self._state_lock:
            failed_token = self._token
        try:
            return self._request(name, payload)
        except PluginWorkerError as error:
            if error.code != "PLUGIN_CALL_TIMEOUT":
                raise
            if recovery == "sync_return":
                return self._restart_after_timeout(failed_token)
            if recovery == "sync_raise":
                try:
                    self._restart_after_timeout(failed_token)
                except PluginWorkerError:
                    pass
            elif recovery == "async_raise":
                self._restart_after_timeout_async(failed_token)
            else:
                raise RuntimeError("unknown plugin recovery policy") from error
            raise

    def _restart_after_timeout(self, failed_token: str) -> dict[str, Any]:
        """Rebuild a killed Worker and restore persisted desired state in this generation."""

        return self._rebuild_worker(failed_token, graceful=False)

    def _rebuild_worker(self, failed_token: str, *, graceful: bool) -> dict[str, Any]:
        """Replace one Worker, preserving graceful cleanup for management rebuilds."""

        with self._restart_lock:
            with self._state_lock:
                if self._closed or self._quiescing:
                    raise PluginWorkerError(
                        "GENERATION_INVALIDATED",
                        "插件 generation 已失效。",
                    )
                if self._token != failed_token:
                    load_done = self._load_done
                    process = None
                else:
                    process = self._process
                    load_done = None
                registry = self._tool_registry
                runtime = self._runtime

            if process is not None:
                if graceful:
                    with self._state_lock:
                        if self._process is process and self._token == failed_token:
                            self._state = "stopping"
                            self._reason_code = "WORKER_REBUILDING"
                            self._invalidate_contributions_locked()
                    try:
                        self._request(
                            "worker.close",
                            {},
                            timeout=CLOSE_TIMEOUT_SECONDS,
                            terminate_on_timeout=False,
                        )
                    except PluginWorkerError:
                        pass
                self._finish_terminated_process(process)
                with self._state_lock:
                    if self._closed or self._quiescing:
                        raise PluginWorkerError(
                            "GENERATION_INVALIDATED",
                            "插件 generation 已失效。",
                        )
                    if self._token == failed_token:
                        self._process = None
                        self._reader = None
                        self._initializer = None
                        self._binders = []
                        self._invalidate_contributions_locked()
                        self._spawn_worker_locked()
                    load_done = self._load_done

            assert load_done is not None
            if not load_done.wait(INITIALIZE_TIMEOUT_SECONDS):
                raise PluginWorkerError(
                    "PLUGIN_INITIALIZE_TIMEOUT",
                    "插件 worker 重建超时。",
                    retryable=True,
                )
            snapshot = self.wait_until_loaded(timeout=0)
            if registry is not None and runtime is not None:
                self.bind_runtime(registry, runtime)
            return snapshot

    def _restart_after_timeout_async(self, failed_token: str) -> None:
        """Recover a timed-out callback without extending the caller's deadline."""

        def rebuild() -> None:
            try:
                self._restart_after_timeout(failed_token)
            except PluginWorkerError:
                return

        thread = threading.Thread(
            target=rebuild,
            name="sakura-plugin-worker-timeout-rebuild",
            daemon=True,
        )
        with self._state_lock:
            if self._closed or self._quiescing:
                return
            self._binders.append(thread)
        thread.start()

    def _finish_terminated_process(self, process: subprocess.Popen[bytes]) -> None:
        if process.poll() is None:
            terminate_process_tree(process, timeout=0.5)
        for stream in (process.stdin, process.stdout):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass
        threads = [self._reader, self._initializer, *self._binders]
        for thread in threads:
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout=0.5)

    def _request(
        self,
        name: str,
        payload: Mapping[str, Any],
        *,
        timeout: float | None = None,
        allow_closed: bool = False,
        terminate_on_timeout: bool = True,
    ) -> object:
        deadline = time.monotonic() + (self._call_timeout if timeout is None else timeout)
        if not self._pending_slots.acquire(timeout=max(0.0, deadline - time.monotonic())):
            raise PluginWorkerError("PLUGIN_QUEUE_FULL", "插件调用队列已满。", retryable=True)
        request_id = secrets.token_hex(12)
        pending = _Pending()
        try:
            with self._state_lock:
                process = self._process
                if (self._closed and not allow_closed) or process is None or process.poll() is not None:
                    raise PluginWorkerError("GENERATION_INVALIDATED", "插件 generation 已失效。")
                self._pending[request_id] = pending
            message = {
                "generationId": self._generation_id,
                "token": self._token,
                "id": request_id,
                "name": name,
                "payload": dict(payload),
            }
            try:
                assert process.stdin is not None
                with self._writer_lock:
                    _write_private_frame(process.stdin, message)
            except (BrokenPipeError, OSError, ValueError) as error:
                raise PluginWorkerError("PLUGIN_WORKER_UNAVAILABLE", "插件 worker 不可用。", retryable=True) from error
            if not pending.done.wait(max(0.0, deadline - time.monotonic())):
                if terminate_on_timeout:
                    self._terminate_unresponsive_worker("PLUGIN_CALL_TIMEOUT")
                raise PluginWorkerError("PLUGIN_CALL_TIMEOUT", "插件调用超时。", retryable=True)
            if pending.error is not None:
                raise pending.error
            return _clone_json(pending.payload)
        finally:
            with self._state_lock:
                self._pending.pop(request_id, None)
            self._pending_slots.release()

    def _read_loop(
        self,
        process: subprocess.Popen[bytes],
        token: str,
        load_done: threading.Event,
    ) -> None:
        assert process.stdout is not None
        failure_code = "PLUGIN_WORKER_EOF"
        try:
            while True:
                response = _read_private_frame(process.stdout)
                if response is None:
                    break
                if response.get("kind") == "host.request":
                    if not self._handle_host_request(response, process, token):
                        failure_code = "PLUGIN_PROTOCOL_INVALID"
                        break
                    continue
                if (
                    response.get("generationId") != self._generation_id
                    or response.get("token") != token
                    or not isinstance(response.get("id"), str)
                    or not isinstance(response.get("ok"), bool)
                ):
                    failure_code = "PLUGIN_PROTOCOL_INVALID"
                    break
                with self._state_lock:
                    pending = self._pending.get(response["id"])
                if pending is None or pending.done.is_set():
                    failure_code = "PLUGIN_RESPONSE_UNKNOWN"
                    break
                if response["ok"]:
                    pending.payload = response.get("payload")
                else:
                    raw = response.get("error") if isinstance(response.get("error"), Mapping) else {}
                    pending.error = PluginWorkerError(
                        str(raw.get("code", "PLUGIN_CALL_FAILED")),
                        "插件调用失败。",
                        retryable=bool(raw.get("retryable")),
                    )
                pending.done.set()
        except PluginWorkerError as error:
            failure_code = error.code
        except (OSError, ValueError, UnicodeError, json.JSONDecodeError):
            failure_code = "PLUGIN_PROTOCOL_INVALID"
        finally:
            with self._state_lock:
                if not self._closed and self._process is process and self._token == token:
                    self._state = "degraded"
                    self._reason_code = failure_code
                    self._invalidate_contributions_locked()
                    self._fail_pending_locked(failure_code)
            load_done.set()

    def _handle_host_request(
        self,
        request: Mapping[str, Any],
        process: subprocess.Popen[bytes],
        token: str,
    ) -> bool:
        request_id = request.get("id")
        payload = request.get("payload")
        valid = (
            request.get("generationId") == self._generation_id
            and request.get("token") == token
            and isinstance(request_id, str)
            and request.get("name") == "host.call"
            and isinstance(payload, Mapping)
        )
        if not valid:
            return False
        service_key = payload.get("serviceKey")
        method = payload.get("method")
        args = payload.get("args")
        if (
            not isinstance(service_key, str)
            or not isinstance(method, str)
            or not isinstance(args, list)
            or len(args) > 32
        ):
            return False
        with self._state_lock:
            host_services = self._host_services
        try:
            if host_services is None:
                raise RuntimeError("HOST_SERVICE_UNAVAILABLE")
            result = getattr(host_services, "call")(service_key, method, args)
            result = _clone_json(result)
            response = {
                "kind": "host.response",
                "generationId": self._generation_id,
                "token": token,
                "id": request_id,
                "ok": True,
                "payload": result,
            }
        except Exception as error:  # noqa: BLE001 - only a stable code crosses the bridge
            code = getattr(error, "code", "HOST_CALL_FAILED")
            if not isinstance(code, str) or not code or len(code) > 80:
                code = "HOST_CALL_FAILED"
            response = {
                "kind": "host.response",
                "generationId": self._generation_id,
                "token": token,
                "id": request_id,
                "ok": False,
                "error": {"code": code, "retryable": False},
            }
        if process.stdin is None:
            return False
        try:
            with self._writer_lock:
                _write_private_frame(process.stdin, response)
        except (BrokenPipeError, OSError, ValueError):
            return False
        return True

    def _host_context_changed(
        self,
        providers: list[ContextProviderContribution],
    ) -> None:
        with self._state_lock:
            runtime = self._runtime
        if runtime is None:
            return
        try:
            getattr(runtime, "set_context_providers")(providers)
        except Exception:
            pass

    def _fail_pending_locked(self, code: str) -> None:
        for pending in self._pending.values():
            if pending.done.is_set():
                continue
            pending.error = PluginWorkerError(code, "插件 worker 不可用。", retryable=True)
            pending.done.set()

    def _terminate_unresponsive_worker(self, code: str) -> None:
        with self._state_lock:
            process = self._process
            if process is None or process.poll() is not None:
                return
            self._state = "degraded"
            self._reason_code = code
            self._invalidate_contributions_locked()
        try:
            terminate_process_tree(process, timeout=0.5)
        except OSError:
            pass

    def _invalidate_contributions_locked(self) -> None:
        registry = self._tool_registry
        self._bound = False
        host_services = self._host_services
        if host_services is not None:
            try:
                getattr(host_services, "clear")()
            except Exception:
                pass
        if registry is not None:
            try:
                getattr(registry, "set_event_emitter")(None)
            except Exception:
                pass


def _context_request_mapping(request: ContextRequest) -> dict[str, Any]:
    value = asdict(request)
    value["recent_messages"] = [dict(item) for item in value.get("recent_messages", [])]
    return value


def _clone_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    cloned = _clone_json(value)
    return dict(cloned) if isinstance(cloned, Mapping) else {}


def _clone_json(value: object) -> object:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _write_private_frame(stream: BinaryIO, message: Mapping[str, Any]) -> None:
    payload = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if not payload or len(payload) > MAX_PRIVATE_FRAME_BYTES:
        raise PluginWorkerError("PLUGIN_FRAME_TOO_LARGE", "插件消息超过限制。")
    stream.write(struct.pack(">I", len(payload)))
    stream.write(payload)
    stream.flush()


def _read_private_frame(stream: BinaryIO) -> dict[str, Any] | None:
    header = _read_exact(stream, 4, clean_eof=True)
    if header is None:
        return None
    length = struct.unpack(">I", header)[0]
    if length < 2 or length > MAX_PRIVATE_FRAME_BYTES:
        raise PluginWorkerError("PLUGIN_FRAME_INVALID", "插件消息帧无效。")
    payload = _read_exact(stream, length)
    assert payload is not None
    value = json.loads(payload.decode("utf-8"))
    if not isinstance(value, dict):
        raise PluginWorkerError("PLUGIN_FRAME_INVALID", "插件消息必须是 object。")
    return value


def _read_exact(stream: BinaryIO, length: int, *, clean_eof: bool = False) -> bytes | None:
    chunks = bytearray()
    while len(chunks) < length:
        chunk = stream.read(length - len(chunks))
        if not chunk:
            if clean_eof and not chunks:
                return None
            raise PluginWorkerError("PLUGIN_FRAME_TRUNCATED", "插件消息帧不完整。")
        chunks.extend(chunk)
    return bytes(chunks)


__all__ = [
    "PluginWorkerClient",
    "PluginWorkerError",
]
