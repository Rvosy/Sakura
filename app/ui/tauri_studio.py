from __future__ import annotations

import json
import os
import sys
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QThread, QTimer, Signal

from app.config.character_studio import CharacterStudioService
from app.ui.screen_color_picker import pick_screen_color
from app.ui.tauri_settings import TauriRpcWorker, _shutdown_rpc_maps
from app.ui.theme import DEFAULT_THEME_SETTINGS, THEME_COLOR_FIELDS, theme_to_mapping

TAURI_STUDIO_BIN_ENV = "SAKURA_TAURI_STUDIO_BIN"
TAURI_STUDIO_PROTOCOL_VERSION = 1
TAURI_STUDIO_RPC_MARKER = "@@SAKURA_STUDIO_RPC@@"
TAURI_STUDIO_RPC_RESULT_MARKER = "@@SAKURA_STUDIO_RPC_RESULT@@"
TAURI_STUDIO_CONTROL_MARKER = "@@SAKURA_STUDIO_CONTROL@@"
STUDIO_FOCUS_RETRY_DELAYS_MS = (100, 300, 700, 1500)


def _is_launchable_tauri_binary(path: Path) -> bool:
    return path.is_file() and (sys.platform == "win32" or os.access(path, os.X_OK))


def resolve_tauri_studio_binary(base_dir: Path, environ: Mapping[str, str] | None = None) -> Path | None:
    env = environ or os.environ
    configured = env.get(TAURI_STUDIO_BIN_ENV)
    if configured:
        path = Path(configured)
        return path if _is_launchable_tauri_binary(path) else None

    root = Path(base_dir)
    binary_name = "sakura-studio.exe" if sys.platform == "win32" else "sakura-studio"
    candidates = (
        root / "tools" / "studio-tauri" / "src-tauri" / "target" / "release" / binary_name,
        root / "tools" / "studio-tauri" / "src-tauri" / "target" / "debug" / binary_name,
    )
    for candidate in candidates:
        if _is_launchable_tauri_binary(candidate):
            return candidate
    return None


def build_tauri_studio_request(
    base_dir: Path,
    *,
    initial_character_id: str = "",
    nonce: str | None = None,
) -> dict[str, Any]:
    service = CharacterStudioService(base_dir)
    theme = theme_to_mapping(DEFAULT_THEME_SETTINGS)
    return {
        "version": TAURI_STUDIO_PROTOCOL_VERSION,
        "nonce": nonce or uuid.uuid4().hex,
        "initial_character_id": str(initial_character_id or ""),
        "characters": service.list_characters(current_character_id=str(initial_character_id or "")),
        "theme": theme,
        "theme_defaults": theme_to_mapping(DEFAULT_THEME_SETTINGS),
        "theme_fields": [
            {"id": field, "label": label}
            for field, label, _default in THEME_COLOR_FIELDS
        ],
    }


def dispatch_tauri_studio_rpc(base_dir: Path, method: str, params: dict[str, Any]) -> dict[str, Any]:
    if not method.startswith("studio."):
        raise ValueError(f"未知 Tauri Studio RPC 方法：{method}")
    if method == "studio.pick_screen_color":
        color = pick_screen_color()
        if color is None:
            return {"cancelled": True}
        return {"color": color}
    service = CharacterStudioService(base_dir)
    if method == "studio.list_characters":
        current_character_id = str(params.get("current_character_id") or "")
        return {"characters": service.list_characters(current_character_id=current_character_id)}
    if method == "studio.open_character":
        return service.open_character(_required_str(params, "character_id"))
    if method == "studio.create_character":
        doc = params.get("doc")
        if not isinstance(doc, dict):
            raise ValueError("studio.create_character 需要 doc 对象。")
        return service.create_character(doc)
    if method == "studio.save_draft":
        doc = params.get("doc")
        if not isinstance(doc, dict):
            raise ValueError("studio.save_draft 需要 doc 对象。")
        return service.save_draft(doc, _required_path(params, "package_dir"))
    if method == "studio.save_workspace_draft":
        doc = params.get("doc")
        if not isinstance(doc, dict):
            raise ValueError("studio.save_workspace_draft 需要 doc 对象。")
        return service.save_workspace_draft(_required_str(params, "workspace_id"), doc)
    if method == "studio.save_character":
        doc = params.get("doc")
        if not isinstance(doc, dict):
            raise ValueError("studio.save_character 需要 doc 对象。")
        return service.save_character(
            doc,
            _workspace_reference(params),
            current_character_id=str(params.get("current_character_id") or ""),
        )
    if method == "studio.import_portrait":
        return service.import_portrait(
            _workspace_reference(params),
            _required_path(params, "path"),
            label=str(params.get("label") or "default"),
        )
    if method == "studio.import_portrait_folder":
        return service.import_portrait_folder(
            _workspace_reference(params),
            _required_path(params, "path"),
        )
    if method == "studio.import_voice_model":
        return service.import_voice_model(
            _workspace_reference(params),
            _required_path(params, "path"),
            model_type=_required_str(params, "model_type"),
        )
    if method == "studio.import_reference_audio":
        return service.import_reference_audio(
            _workspace_reference(params),
            _required_path(params, "path"),
        )
    if method == "studio.import_reference_audio_folder":
        return service.import_reference_audio_folder(
            _workspace_reference(params),
            _required_path(params, "path"),
            ref_lang=str(params.get("ref_lang") or "ja"),
        )
    if method == "studio.load_reference_audio_preview":
        return service.load_reference_audio_preview(
            _workspace_reference(params),
            _required_str(params, "relative_path"),
        )
    if method == "studio.discard_draft":
        return service.discard_draft(
            _required_str(params, "workspace_id"),
            current_character_id=str(params.get("current_character_id") or ""),
        )
    if method == "studio.release_workspace":
        return service.release_workspace(_required_str(params, "workspace_id"))
    if method == "studio.export_archive":
        return service.export_archive(
            _workspace_reference(params),
            _required_path(params, "path"),
            include_voice=bool(params.get("include_voice")),
        )
    raise ValueError(f"未知 Tauri Studio RPC 方法：{method}")


def _workspace_reference(params: dict[str, Any]) -> str | Path:
    workspace_id = str(params.get("workspace_id") or "").strip()
    if workspace_id:
        return workspace_id
    return _required_path(params, "package_dir")


class TauriStudioProcess(QObject):
    closed = Signal()
    failed = Signal(str)

    def __init__(self, base_dir: Path, *, initial_character_id: str = "", parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.base_dir = Path(base_dir)
        self.initial_character_id = str(initial_character_id or "")
        self._process: QProcess | None = None
        self._request_payload = b""
        self._stdout_buffer = ""
        self._done = False
        self._startup_focus_complete = False
        self._rpcs: dict[str, tuple[QThread, QObject]] = {}

    def start(self) -> bool:
        binary = resolve_tauri_studio_binary(self.base_dir)
        if binary is None:
            return False
        request = build_tauri_studio_request(
            self.base_dir,
            initial_character_id=self.initial_character_id,
        )
        process = QProcess(self)
        process.setProgram(str(binary))
        process.setArguments([])
        process.setWorkingDirectory(str(self.base_dir))
        process.setProcessEnvironment(QProcessEnvironment.systemEnvironment())
        process.started.connect(self._handle_started)
        process.finished.connect(self._handle_finished)
        process.errorOccurred.connect(self._handle_error)
        process.readyReadStandardOutput.connect(self._handle_stdout)
        self._process = process
        self._request_payload = json.dumps(request, ensure_ascii=False).encode("utf-8")
        self._startup_focus_complete = False
        process.start()
        return not self._done and self._process is process

    def focus_window(self) -> bool:
        from app.ui.tauri_settings import _restore_windows_for_pid

        process = self._process
        if process is None:
            return False
        control_sent = self._send_window_control("focus")
        if sys.platform != "win32":
            return control_sent
        try:
            pid = int(process.processId())
        except (RuntimeError, TypeError, ValueError):
            return False
        return pid > 0 and _restore_windows_for_pid(pid, force_foreground=True)

    def _send_window_control(self, action: str) -> bool:
        process = self._process
        if process is None or self._done:
            return False
        line = TAURI_STUDIO_CONTROL_MARKER + json.dumps({"action": action}) + "\n"
        try:
            return process.write(line.encode("utf-8")) >= 0
        except (AttributeError, OSError, RuntimeError, TypeError):
            return False

    def _handle_started(self) -> None:
        self._send_request()
        process = self._process
        if process is None or self._done or sys.platform != "win32":
            return
        self._startup_focus_complete = False
        for delay_ms in STUDIO_FOCUS_RETRY_DELAYS_MS:
            QTimer.singleShot(
                delay_ms,
                lambda active_process=process: self._try_startup_focus(active_process),
            )

    def _try_startup_focus(self, process: object) -> None:
        if (
            self._done
            or self._process is not process
            or self._startup_focus_complete
        ):
            return
        if self.focus_window():
            self._startup_focus_complete = True

    def shutdown(self, timeout_ms: int = 1000) -> None:
        self._done = True
        process = self._process
        if process is not None:
            try:
                process.closeWriteChannel()
            except RuntimeError:
                pass
            try:
                if process.state() != QProcess.ProcessState.NotRunning:
                    process.terminate()
                    if not process.waitForFinished(timeout_ms):
                        process.kill()
                        process.waitForFinished(timeout_ms)
            except RuntimeError:
                pass
        self._process = None
        _shutdown_rpc_maps((self._rpcs,), total_wait_ms=timeout_ms)

    def _send_request(self) -> None:
        process = self._process
        if process is None or self._done:
            return
        try:
            if process.write(self._request_payload + b"\n") < 0:
                raise OSError("write returned a negative byte count")
        except (OSError, RuntimeError) as exc:
            self._done = True
            self.failed.emit(f"Tauri 角色工作室请求发送失败：{exc}")

    def _handle_stdout(self, *, flush: bool = False) -> None:
        process = self._process
        if process is None:
            return
        chunk = bytes(process.readAllStandardOutput()).decode("utf-8", errors="replace")
        if not chunk and not flush:
            return
        self._stdout_buffer += chunk
        *lines, self._stdout_buffer = self._stdout_buffer.split("\n")
        if flush and self._stdout_buffer:
            lines.append(self._stdout_buffer)
            self._stdout_buffer = ""
        for line in lines:
            stripped = line.strip()
            if stripped.startswith(TAURI_STUDIO_RPC_MARKER):
                self._handle_rpc_request(stripped[len(TAURI_STUDIO_RPC_MARKER):])

    def _handle_rpc_request(self, payload: str) -> None:
        request: dict[str, Any] | None = None
        try:
            parsed = json.loads(payload)
            if not isinstance(parsed, dict):
                raise ValueError("RPC 请求必须是对象。")
            request = parsed
            request_id = request.get("id")
            method = request.get("method")
            params = request.get("params", {})
            if not isinstance(request_id, str) or not request_id:
                raise ValueError("RPC 请求缺少 id。")
            if not isinstance(method, str) or not method:
                raise ValueError("RPC 请求缺少 method。")
            if not isinstance(params, dict):
                raise ValueError("RPC params 必须是对象。")
        except Exception as exc:  # noqa: BLE001 - UI RPC boundary reports readable errors.
            request_id = ""
            if isinstance(request, dict):
                request_id = str(request.get("id") or "")
            self._send_rpc_response(request_id, ok=False, error=str(exc))
            return
        if method == "studio.pick_screen_color":
            try:
                result = dispatch_tauri_studio_rpc(self.base_dir, method, params)
            except Exception as exc:  # noqa: BLE001
                self._send_rpc_response(request_id, ok=False, error=str(exc))
                return
            self._send_rpc_response(request_id, ok=True, result=result)
            return
        worker = TauriRpcWorker(
            lambda rpc_method, rpc_params: dispatch_tauri_studio_rpc(
                self.base_dir,
                rpc_method,
                rpc_params,
            ),
            method,
            params,
        )
        self._start_rpc_worker(request_id, worker)

    def _start_rpc_worker(self, request_id: str, worker: TauriRpcWorker) -> None:
        thread = QThread()
        worker.moveToThread(thread)
        self._rpcs[request_id] = (thread, worker)
        thread.started.connect(worker.run)
        worker.succeeded.connect(
            lambda result, rid=request_id: self._send_rpc_response(
                rid,
                ok=True,
                result=result if isinstance(result, dict) else {"value": result},
            )
        )
        worker.failed.connect(
            lambda message, rid=request_id: self._send_rpc_response(
                rid,
                ok=False,
                error=str(message),
            )
        )
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda rid=request_id: self._rpcs.pop(rid, None))
        thread.start()

    def _send_rpc_response(
        self,
        request_id: str,
        *,
        ok: bool,
        result: dict[str, Any] | None = None,
        error: str = "",
    ) -> None:
        process = self._process
        if process is None:
            return
        payload = {
            "id": request_id,
            "ok": ok,
            "result": result if ok else None,
            "error": "" if ok else error,
        }
        line = TAURI_STUDIO_RPC_RESULT_MARKER + json.dumps(payload, ensure_ascii=False) + "\n"
        process.write(line.encode("utf-8"))

    def _handle_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        self._handle_stdout(flush=True)
        if self._done:
            self._process = None
            return
        self._done = True
        self._process = None
        _shutdown_rpc_maps((self._rpcs,), total_wait_ms=0)
        if exit_status != QProcess.ExitStatus.NormalExit or exit_code != 0:
            self.failed.emit(
                "Tauri 角色工作室异常退出"
                f"（exit_code={exit_code}），请重建角色工作室或检查 {TAURI_STUDIO_BIN_ENV}。"
            )
            return
        self.closed.emit()

    def _handle_error(self, error: QProcess.ProcessError) -> None:
        if self._done:
            return
        self._done = True
        self._process = None
        _shutdown_rpc_maps((self._rpcs,), total_wait_ms=0)
        self.failed.emit(f"Tauri 角色工作室启动失败：{error.name}")


def _required_str(mapping: dict[str, Any], key: str) -> str:
    value = str(mapping.get(key) or "").strip()
    if not value:
        raise ValueError(f"缺少字段：{key}")
    return value


def _required_path(mapping: dict[str, Any], key: str) -> Path:
    return Path(_required_str(mapping, key))
