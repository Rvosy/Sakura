"""Declarative composer tool dock boundary for one Runtime v2 Core generation."""

from __future__ import annotations

import hmac
import re
from collections.abc import Callable, Mapping
from typing import Any

from app.core_host.protocol import response


COMPOSER_TOOL_REQUEST_NAMES = frozenset(
    {"ui.composer_tools.get", "ui.composer_tools.invoke"}
)
_PUBLIC_TOOL_ID = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}:[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$"
)


class ComposerToolsError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def public_error(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.code in {"COMPOSER_TOOLS_NOT_READY", "COMPOSER_TOOL_INVOKE_FAILED"},
            "details": {"feature": "ui.composer-tools", "field": ""},
        }


class ComposerToolsBoundary:
    def __init__(
        self,
        generation_id: str,
        generation_credential: str,
        *,
        application_provider: Callable[[], object | None],
    ) -> None:
        self._generation_id = generation_id
        self._generation_credential = generation_credential
        self._application_provider = application_provider

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        supplied = request.get("generationCredential")
        if (
            request.get("generationId") != self._generation_id
            or not isinstance(supplied, str)
            or not hmac.compare_digest(supplied, self._generation_credential)
        ):
            raise RuntimeError("GENERATION_IDENTITY_MISMATCH")
        try:
            payload = request.get("payload")
            if not isinstance(payload, Mapping):
                raise ComposerToolsError("INVALID_REQUEST", "扩展工具请求格式无效。")
            application = self._application_provider()
            if application is None:
                raise ComposerToolsError("COMPOSER_TOOLS_NOT_READY", "扩展工具仍在初始化。")
            name = request.get("name")
            if name == "ui.composer_tools.get":
                if payload:
                    raise ComposerToolsError("INVALID_REQUEST", "扩展工具读取请求必须为空。")
                result: object = {
                    "schemaVersion": 1,
                    "coreGenerationId": self._generation_id,
                    "tools": getattr(application, "composer_tools")(),
                }
            elif name == "ui.composer_tools.invoke":
                if set(payload) != {"toolId"}:
                    raise ComposerToolsError("INVALID_REQUEST", "扩展工具调用格式无效。")
                tool_id = payload.get("toolId")
                if not isinstance(tool_id, str) or not _PUBLIC_TOOL_ID.fullmatch(tool_id):
                    raise ComposerToolsError("COMPOSER_TOOL_ID_INVALID", "扩展工具标识无效。")
                result = getattr(application, "invoke_composer_tool")(tool_id)
            else:
                raise ComposerToolsError("INVALID_REQUEST", "扩展工具请求不存在。")
            return response(
                request,
                generation_id=self._generation_id,
                generation_credential=self._generation_credential,
                payload=result,
            )
        except ComposerToolsError as error:
            return response(
                request,
                generation_id=self._generation_id,
                generation_credential=self._generation_credential,
                error=error.public_error(),
            )
        except Exception as error:  # noqa: BLE001 - private failures stay behind the boundary
            code = str(getattr(error, "code", "COMPOSER_TOOL_INVOKE_FAILED"))
            public = ComposerToolsError(
                code if code.startswith("COMPOSER_TOOL") else "COMPOSER_TOOL_INVOKE_FAILED",
                "扩展工具运行失败。",
            )
            return response(
                request,
                generation_id=self._generation_id,
                generation_credential=self._generation_credential,
                error=public.public_error(),
            )


__all__ = ["COMPOSER_TOOL_REQUEST_NAMES", "ComposerToolsBoundary", "ComposerToolsError"]
