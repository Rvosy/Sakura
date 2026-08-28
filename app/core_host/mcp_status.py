"""Read-only, sanitized MCP runtime status for one Core generation."""

from __future__ import annotations

import hmac
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from app.agent.mcp.config import MCPConfig, load_mcp_config
from app.core_host.protocol import response
from app.storage.paths import StoragePaths


MCP_STATUS_REQUEST_NAMES = frozenset({"mcp.status.get"})
_SERVER_ID = re.compile(r"[^A-Za-z0-9_.-]+")
_SERVER_STATES = frozenset(
    {"disabled", "starting", "ready", "degraded", "stopping", "stopped"}
)


class MCPStatusBoundary:
    def __init__(
        self,
        generation_id: str,
        generation_credential: str,
        app_root: Path,
        *,
        session_provider: Callable[[], object | None],
    ) -> None:
        self._generation_id = generation_id
        self._generation_credential = generation_credential
        self._mcp_path = StoragePaths(app_root).mcp_config()
        self._session_provider = session_provider

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        supplied = request.get("generationCredential")
        if (
            request.get("generationId") != self._generation_id
            or not isinstance(supplied, str)
            or not hmac.compare_digest(supplied, self._generation_credential)
        ):
            raise RuntimeError("GENERATION_IDENTITY_MISMATCH")
        payload = request.get("payload")
        if request.get("name") != "mcp.status.get" or not isinstance(payload, Mapping) or payload:
            return response(
                request,
                generation_id=self._generation_id,
                generation_credential=self._generation_credential,
                protocol_minor=2,
                error={
                    "code": "INVALID_REQUEST",
                    "message": "MCP 状态请求格式无效。",
                    "retryable": False,
                    "details": {},
                },
            )
        return response(
            request,
            generation_id=self._generation_id,
            generation_credential=self._generation_credential,
            protocol_minor=2,
            payload=self.snapshot(),
        )

    def snapshot(self) -> dict[str, object]:
        session = self._session_provider()
        provider = getattr(session, "mcp_provider", None) if session is not None else None
        status_snapshot = getattr(provider, "status_snapshot", None)
        if callable(status_snapshot):
            status = _project_status(status_snapshot())
        elif not self._mcp_path.exists():
            status = {"configState": "missing", "reasonCode": "CONFIG_MISSING", "servers": []}
        else:
            try:
                status = _preview_status(load_mcp_config(self._mcp_path))
            except Exception:  # noqa: BLE001 - configuration details stay inside Core
                status = {"configState": "invalid", "reasonCode": "CONFIG_INVALID", "servers": []}
        return {"schemaVersion": 1, **status}


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
                    "toolCount": count
                    if isinstance(count, int) and not isinstance(count, bool) and 0 <= count <= 512
                    else 0,
                }
            )
    return {"configState": config_state, "reasonCode": reason, "servers": servers}


def _public_server_id(value: str) -> str:
    normalized = _SERVER_ID.sub("_", value.strip())[:64].strip("._-")
    return normalized or "server"


def _reason_code(value: object, default: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 64:
        return default
    if any(
        not (
            character.isascii()
            and (character.isupper() or character.isdigit() or character == "_")
        )
        for character in value
    ):
        return default
    return value


__all__ = ["MCP_STATUS_REQUEST_NAMES", "MCPStatusBoundary"]
