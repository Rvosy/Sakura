"""Settings-owned AI theme requests; generated colors remain an unsaved draft."""
from __future__ import annotations

import base64
import hmac
import json
from pathlib import Path
import threading
import time
import uuid

from app.config.character_loader import CharacterRegistry
from app.config.settings_service import AppSettingsService
from app.core.diagnostics import exception_diagnostics
from app.core.runtime_log import log_event
from app.plugin_sdk.sakura_cancellation import OperationCancelled
from app.plugins.visuals import resolve_resource_path

from .protocol import response


THEME_REQUEST_NAMES = frozenset({"theme.generate", "theme.cancel"})


class ThemeGenerationBoundary:
    def __init__(self, generation_id, generation_credential, user_root, *, application_provider):
        self._generation_id = generation_id
        self._credential = generation_credential
        self._user_root = Path(user_root)
        self._application_provider = application_provider
        self._lock = threading.Lock()
        self._operations = {}

    def _validate(self, request):
        credential = request.get("generationCredential")
        if (request.get("generationId") != self._generation_id or not isinstance(credential, str)
                or not hmac.compare_digest(credential, self._credential)):
            raise RuntimeError("GENERATION_IDENTITY_MISMATCH")
        name, payload = request.get("name"), request.get("payload")
        fields = {"operationId", "characterId"} if name == "theme.generate" else {"operationId"}
        if name not in THEME_REQUEST_NAMES or not isinstance(payload, dict) or set(payload) != fields:
            raise ValueError("AI 配色请求格式无效。")
        if not isinstance(payload["operationId"], str):
            raise ValueError("AI 配色任务标识无效。")
        uuid.UUID(payload["operationId"])
        if name == "theme.generate" and (not isinstance(payload["characterId"], str) or not payload["characterId"]):
            raise ValueError("请先选择一个角色。")
        return payload

    def reserve(self, request):
        payload = self._validate(request)
        with self._lock:
            previous = self._operations.get(payload["operationId"])
            if previous is not None:
                if previous[0] != request["id"]:
                    raise ValueError("AI 配色任务已存在。")
                return previous[1]
            cancelled = threading.Event()
            self._operations[payload["operationId"]] = (request["id"], cancelled)
            return cancelled

    def abandon(self, request):
        payload = request.get("payload")
        operation_id = payload.get("operationId") if isinstance(payload, dict) else None
        if not isinstance(operation_id, str):
            return
        with self._lock:
            operation = self._operations.get(operation_id)
            if operation is not None and operation[0] == request["id"]:
                operation[1].set()
                self._operations.pop(operation_id)

    def handle(self, request):
        try:
            payload = self._validate(request)
            if request["name"] == "theme.cancel":
                with self._lock:
                    operation = self._operations.get(payload["operationId"])
                    if operation is not None:
                        operation[1].set()
                result = {"cancelled": operation is not None}
            else:
                cancelled = self.reserve(request)
                try:
                    result = self._generate(payload, cancelled)
                finally:
                    self.abandon(request)
            return self._response(request, payload=result)
        except Exception as error:
            code = "OPERATION_CANCELLED" if isinstance(error, OperationCancelled) else getattr(error, "code", "THEME_GENERATION_FAILED")
            diagnostic = exception_diagnostics(error, reason_code=code, stage=request.get("name", "theme.generate"))
            if code != "OPERATION_CANCELLED":
                log_event("Theme", "AI 配色生成失败", diagnostic, event="theme.generation.failed", severity="error")
            return self._response(request, error={
                "code": code, "message": "配色生成已取消。" if code == "OPERATION_CANCELLED" else str(error),
                "retryable": False, "details": {"feature": "appearance.theme_ai", **diagnostic},
            })

    def _response(self, request, **kwargs):
        return response(request, generation_id=self._generation_id,
                        generation_credential=self._credential, protocol_minor=2, **kwargs)

    def _generate(self, payload, cancelled):
        def check():
            if cancelled.is_set():
                raise OperationCancelled()

        check()
        registry = CharacterRegistry(self._user_root)
        settings = AppSettingsService(self._user_root)
        character_id = payload["characterId"]
        if settings.load_current_character_id(registry) != character_id:
            raise ValueError("角色已切换，请为当前角色重新生成配色。")
        character = registry.get(character_id)
        resource = character.current_visual_resource
        if resource is None:
            raise ValueError("当前角色没有默认形态图片，无法生成配色。")
        application = self._application_provider()
        if application is None:
            raise ValueError("模型与表现服务尚未就绪，请稍后重试。")
        root = resolve_resource_path(character.package_dir, resource.root)
        entry = resolve_resource_path(root, resource.entry)
        raw = json.loads(entry.read_text(encoding="utf-8")) if entry.suffix.lower() == ".json" else None
        relative = application.visuals.preview_image(resource, raw, character.visual_providers.get(resource.id))
        if relative is None:
            raise ValueError("当前角色的默认形态没有提供预览图片，无法生成配色。")
        path = resolve_resource_path(root, relative)
        media_type = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                      ".webp": "image/webp", ".gif": "image/gif"}.get(path.suffix.lower())
        if media_type is None:
            raise ValueError("角色预览图片需使用 PNG、JPEG、WebP 或 GIF 格式。")
        image_url = f"data:{media_type};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"
        check()
        started = time.monotonic()
        log_event("Theme", "开始生成 AI 配色", {"character_id": character_id,
                  "operation_id": payload["operationId"]}, event="theme.generation.started")
        colors = application.generate_theme(payload["operationId"], character.display_name, image_url, cancel_checker=check)
        check()
        if settings.load_current_character_id(CharacterRegistry(self._user_root)) != character_id:
            raise OperationCancelled()
        log_event("Theme", "AI 配色已生成", {"character_id": character_id,
                  "operation_id": payload["operationId"], "elapsed_ms": int((time.monotonic() - started) * 1000)},
                  event="theme.generation.completed")
        return {"characterId": character_id, "generationId": self._generation_id, "themeTokens": colors}

    def close(self):
        with self._lock:
            for _, cancelled in self._operations.values():
                cancelled.set()
