"""Qt-free MCP settings and runtime-status boundary for one Core generation."""

from __future__ import annotations

import hmac
import re
import sys
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import yaml

from app.agent.mcp.config import MCPConfig, load_mcp_config
from app.agent.mcp.settings import (
    DESKTOP_MCP_EXPERIMENTAL_TEXT,
    MCPRuntimeSettings,
    apply_mcp_runtime_settings,
    resolve_desktop_mcp,
)
from app.core_host.protocol import response
from app.storage.atomic import atomic_write_text
from app.storage.paths import StoragePaths


MCP_CAPABILITY = "assistant.mcp-v1"
MCP_SETTINGS_REQUEST_NAMES = frozenset({"mcp.settings.get", "mcp.settings.save"})
CURRENT_CONFIG_VERSION = 4
_SERVER_ID = re.compile(r"[^A-Za-z0-9_.-]+")
_SERVER_STATES = frozenset(
    {"disabled", "starting", "ready", "degraded", "stopping", "stopped"}
)


class MCPSettingsError(ValueError):
    def __init__(self, code: str, message: str, *, field: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field

    def public_error(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.code == "CONFIG_SAVE_FAILED",
            "details": {"feature": "tools.desktop_mcp", "field": self.field},
        }


class MCPSettingsBoundary:
    def __init__(
        self,
        generation_id: str,
        generation_credential: str,
        app_root: Path,
        *,
        session_provider: Callable[[], object | None],
        platform: str | None = None,
    ) -> None:
        self._generation_id = generation_id
        self._generation_credential = generation_credential
        self._app_root = Path(app_root)
        self._system_path = StoragePaths(app_root).system_config()
        self._mcp_path = StoragePaths(app_root).mcp_config()
        self._session_provider = session_provider
        self._platform = sys.platform if platform is None else platform
        self._save_lock = threading.Lock()

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        supplied = request.get("generationCredential")
        if (
            request.get("generationId") != self._generation_id
            or not isinstance(supplied, str)
            or not hmac.compare_digest(supplied, self._generation_credential)
        ):
            raise RuntimeError("GENERATION_IDENTITY_MISMATCH")
        try:
            name = request.get("name")
            payload = request.get("payload")
            if not isinstance(payload, Mapping):
                raise MCPSettingsError("INVALID_REQUEST", "MCP 设置请求格式无效。")
            if name == "mcp.settings.get":
                if payload:
                    raise MCPSettingsError("INVALID_REQUEST", "MCP 设置读取请求必须为空。")
                result = self.snapshot()
            elif name == "mcp.settings.save":
                if set(payload) != {"settings"}:
                    raise MCPSettingsError("INVALID_REQUEST", "MCP 设置保存请求格式无效。")
                result = self.save(payload["settings"])
            else:
                raise MCPSettingsError("UNKNOWN_COMMAND", "不支持的 MCP 设置命令。")
            return response(
                request,
                generation_id=self._generation_id,
                generation_credential=self._generation_credential,
                protocol_minor=2,
                payload=result,
            )
        except MCPSettingsError as error:
            return response(
                request,
                generation_id=self._generation_id,
                generation_credential=self._generation_credential,
                protocol_minor=2,
                error=error.public_error(),
            )

    def snapshot(self) -> dict[str, object]:
        settings = load_mcp_runtime_settings(self._app_root)
        status = self._runtime_status(settings)
        desktop = resolve_desktop_mcp(self._platform)
        return {
            "schemaVersion": 1,
            "desktop": {
                "supported": desktop is not None,
                "label": desktop.label if desktop is not None else "Desktop MCP",
                "experimentalText": DESKTOP_MCP_EXPERIMENTAL_TEXT,
            },
            "desktopEnabled": settings.desktop_enabled,
            "configState": status["configState"],
            "reasonCode": status["reasonCode"],
            "servers": status["servers"],
        }

    def save(self, raw: object) -> dict[str, object]:
        if not isinstance(raw, Mapping) or set(raw) != {"desktopEnabled"}:
            raise MCPSettingsError("INVALID_REQUEST", "MCP 设置字段无效。")
        enabled = raw.get("desktopEnabled")
        if not isinstance(enabled, bool):
            raise MCPSettingsError(
                "FIELD_INVALID",
                "桌面 MCP 开关必须是布尔值。",
                field="desktopEnabled",
            )
        with self._save_lock:
            document = _read_system_document(self._system_path)
            updated = dict(document)
            mcp = dict(_mapping(updated.get("mcp")))
            mcp["desktop_enabled"] = enabled
            mcp.pop("windows_enabled", None)
            updated["mcp"] = mcp
            try:
                serialized = yaml.safe_dump(
                    updated,
                    allow_unicode=True,
                    sort_keys=False,
                    default_flow_style=False,
                )
                atomic_write_text(self._system_path, serialized)
            except OSError as error:
                raise MCPSettingsError(
                    "CONFIG_SAVE_FAILED",
                    "MCP 设置保存失败，原文件保持不变。",
                ) from error
        result = self.snapshot()
        result.update(saved=True, changePlan="core_restart_required")
        return result

    def _runtime_status(self, settings: MCPRuntimeSettings) -> dict[str, object]:
        session = self._session_provider()
        provider = getattr(session, "mcp_provider", None) if session is not None else None
        status_snapshot = getattr(provider, "status_snapshot", None)
        if callable(status_snapshot):
            return _project_status(status_snapshot())
        if not self._mcp_path.exists():
            return {"configState": "missing", "reasonCode": "CONFIG_MISSING", "servers": []}
        try:
            config = apply_mcp_runtime_settings(load_mcp_config(self._mcp_path), settings)
        except Exception:  # noqa: BLE001 - no config details cross the settings boundary
            return {"configState": "invalid", "reasonCode": "CONFIG_INVALID", "servers": []}
        return _preview_status(config)


def load_mcp_runtime_settings(app_root: Path) -> MCPRuntimeSettings:
    document = _read_system_document(StoragePaths(app_root).system_config())
    mcp = _mapping(document.get("mcp"))
    raw = mcp.get("desktop_enabled", mcp.get("windows_enabled", False))
    return MCPRuntimeSettings(desktop_enabled=raw if isinstance(raw, bool) else False)


def _read_system_document(path: Path) -> dict[str, Any]:
    try:
        if not path.exists():
            return {"config_version": CURRENT_CONFIG_VERSION}
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise MCPSettingsError("CONFIG_READ_ONLY", "系统配置损坏或不可读取。") from error
    if not isinstance(raw, Mapping):
        raise MCPSettingsError("CONFIG_READ_ONLY", "系统配置格式无效。")
    version = raw.get("config_version", CURRENT_CONFIG_VERSION)
    if isinstance(version, bool) or not isinstance(version, int) or version > CURRENT_CONFIG_VERSION:
        raise MCPSettingsError("CONFIG_FUTURE_SCHEMA", "系统配置版本高于当前 Runtime。")
    return dict(raw)


def _preview_status(config: MCPConfig) -> dict[str, object]:
    servers = [
        {
            "serverId": _public_server_id(server.name),
            "transport": server.transport,
            "enabled": bool(config.enabled and server.enabled),
            "state": "starting" if config.enabled and server.enabled else "disabled",
            "reasonCode": "SESSION_NOT_READY" if config.enabled and server.enabled else "SERVER_DISABLED",
            "toolCount": 0,
        }
        for server in config.servers
    ]
    return {
        "configState": "valid",
        "reasonCode": "SESSION_NOT_READY" if any(item["enabled"] for item in servers) else "CONFIG_DISABLED",
        "servers": servers,
    }


def _project_status(raw: object) -> dict[str, object]:
    value = raw if isinstance(raw, Mapping) else {}
    config_state = value.get("configState")
    if config_state not in {"valid", "missing", "invalid"}:
        config_state = "invalid"
    reason = _reason_code(value.get("reasonCode"), "CONFIG_INVALID")
    servers: list[dict[str, object]] = []
    raw_servers = value.get("servers")
    if isinstance(raw_servers, list):
        for item in raw_servers[:16]:
            if not isinstance(item, Mapping):
                continue
            state = item.get("state") if item.get("state") in _SERVER_STATES else "degraded"
            transport = item.get("transport") if item.get("transport") in {"stdio", "sse"} else "stdio"
            count = item.get("toolCount")
            servers.append(
                {
                    "serverId": _public_server_id(str(item.get("serverId", "server"))),
                    "transport": transport,
                    "enabled": bool(item.get("enabled")),
                    "state": state,
                    "reasonCode": _reason_code(item.get("reasonCode"), "STATUS_INVALID"),
                    "toolCount": count if isinstance(count, int) and not isinstance(count, bool) and 0 <= count <= 512 else 0,
                }
            )
    return {"configState": config_state, "reasonCode": reason, "servers": servers}


def _public_server_id(value: str) -> str:
    normalized = _SERVER_ID.sub("_", value.strip())[:64].strip("._-")
    return normalized or "server"


def _reason_code(value: object, default: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 64:
        return default
    if any(not (character.isascii() and (character.isupper() or character.isdigit() or character == "_")) for character in value):
        return default
    return value


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


__all__ = [
    "MCP_CAPABILITY",
    "MCP_SETTINGS_REQUEST_NAMES",
    "MCPSettingsBoundary",
    "MCPSettingsError",
    "load_mcp_runtime_settings",
]
