from __future__ import annotations

import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .server import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    mobile_access_urls,
    run_mobile_server,
)


PLUGIN_ID = "sakura_mobile"
MOBILE_SERVICE = "sakura.mobile"
SETTINGS_SECTION_ID = "sakura_mobile"


class SakuraMobilePlugin:
    """Mobile HTTP endpoint backed by an ordinary Worker-local mobile Service."""

    def __init__(self) -> None:
        self._config: object | None = None
        self._mobile_service: object | None = None
        self._server: Any | None = None
        self._thread: threading.Thread | None = None
        self._last_error = ""
        self._user_root: Path | None = None

    def setup(self, context: object) -> None:
        self._user_root = Path(getattr(context, "data_path")(".")).resolve().parents[2]
        self._config = getattr(context, "config")
        self._mobile_service = getattr(context, "get")(MOBILE_SERVICE)
        getattr(context, "effect")(self.stop)
        getattr(context, "on")("sakura.host.app.started", lambda _event: self.start())
        getattr(self._config, "on_change")(self._apply_config)
        getattr(context, "get")("sakura.host.settings").register(
            _settings_descriptor(),
            load=self.settings_values,
            save=self.save_settings_values,
            actions={"refresh_status": self.refresh_settings_status},
        )

    def config(self) -> dict[str, Any]:
        config = self._require_config()
        return _normalized_config(getattr(config, "get")())

    def settings_values(self) -> dict[str, Any]:
        status = self.status()
        running = "未启动"
        if status["enabled"]:
            if status["running"]:
                running = "运行中"
            elif status["error"]:
                running = "启动失败"
        return {
            "enabled": bool(status["enabled"]),
            "host": str(status["host"]),
            "port": int(status["port"]),
            "token": str(status["token"]),
            "running": running,
            "local_url": str(status.get("local_url") or ""),
            "lan_urls": " ; ".join(status.get("lan_urls") or []) or "未发现内网地址",
            "error": str(status.get("error") or ""),
        }

    def save_settings_values(self, values: Mapping[str, Any]) -> list[str]:
        current = self.config()
        merged = _normalized_config({**current, **dict(values)})
        if merged["enabled"] and not str(values.get("token", current["token"])).strip():
            raise ValueError("启用手机网页端时访问 token 不能为空。")
        updates = {key: merged[key] for key in values if key in merged}
        return getattr(self._require_config(), "update")(updates)

    def refresh_settings_status(self, _values: Mapping[str, Any]) -> dict[str, Any]:
        values = self.settings_values()
        return {
            "values": {
                "running": values["running"],
                "local_url": values["local_url"],
                "lan_urls": values["lan_urls"],
                "error": values["error"],
            }
        }

    def start(self) -> None:
        if self._server is not None:
            return
        config = self.config()
        if not config["enabled"]:
            self._last_error = ""
            return
        mobile_service = self._mobile_service
        if mobile_service is None:
            self._last_error = "移动端聊天服务尚未就绪。"
            return
        if self._user_root is None:
            self._last_error = "移动端存储尚未就绪。"
            return
        try:
            server = run_mobile_server(
                self._user_root,
                mobile_service,
                host=str(config["host"]),
                port=int(config["port"]),
                token=str(config["token"]),
            )
        except OSError:
            self._last_error = "监听失败，请检查地址和端口是否可用。"
            return
        thread = threading.Thread(
            target=server.serve_forever,
            name="SakuraMobilePlugin",
            daemon=True,
        )
        self._server = server
        self._thread = thread
        self._last_error = ""
        thread.start()

    def stop(self) -> None:
        server = self._server
        thread = self._thread
        self._server = None
        self._thread = None
        if server is None:
            return
        try:
            server.shutdown()
            server.server_close()
        finally:
            if thread is not None and thread is not threading.current_thread():
                thread.join()

    def restart(self) -> None:
        self.stop()
        self.start()

    def status(self) -> dict[str, Any]:
        config = self.config()
        return {
            **config,
            "running": self._server is not None,
            "error": self._last_error,
            **mobile_access_urls(
                str(config["host"]),
                int(config["port"]),
                str(config["token"]),
            ),
        }

    def _apply_config(self, _values: Mapping[str, Any]) -> str:
        self.restart()
        config = self.config()
        return "error" if config["enabled"] and self._server is None else "applied"

    def _require_config(self) -> object:
        if self._config is None:
            raise RuntimeError("手机端插件尚未初始化。")
        return self._config


def _settings_descriptor() -> dict[str, Any]:
    return {
        "sectionId": SETTINGS_SECTION_ID,
        "title": "手机端",
        "order": 70,
        "fields": [
            {
                "key": "enabled",
                "label": "启用手机网页端",
                "type": "boolean",
                "default": False,
            },
            {
                "key": "host",
                "label": "监听地址",
                "type": "string",
                "default": DEFAULT_HOST,
                "required": True,
                "maxLength": 255,
            },
            {
                "key": "port",
                "label": "端口",
                "type": "integer",
                "default": DEFAULT_PORT,
                "minimum": 1,
                "maximum": 65535,
            },
            {
                "key": "token",
                "label": "访问 token",
                "type": "password",
                "default": "sakura",
                "required": True,
                "copyable": True,
                "maxLength": 512,
            },
            {
                "key": "running",
                "label": "运行状态",
                "type": "readonly",
                "default": "未启动",
            },
            {
                "key": "local_url",
                "label": "本机链接",
                "type": "readonly",
                "default": "",
                "copyable": True,
            },
            {
                "key": "lan_urls",
                "label": "内网链接",
                "type": "readonly",
                "default": "未发现内网地址",
                "copyable": True,
            },
            {
                "key": "error",
                "label": "错误",
                "type": "readonly",
                "default": "",
            },
        ],
        "actions": [
            {
                "actionId": "refresh_status",
                "label": "刷新状态",
                "description": "刷新手机网页服务的运行状态和访问地址。",
                "danger": False,
            }
        ],
    }


def _normalized_config(value: Mapping[str, Any]) -> dict[str, Any]:
    token = str(value.get("token") or "sakura").strip() or "sakura"
    return {
        "enabled": _as_bool(value.get("enabled"), False),
        "host": str(value.get("host") or DEFAULT_HOST).strip() or DEFAULT_HOST,
        "port": _safe_port(value.get("port"), DEFAULT_PORT),
        "token": token,
    }


def _as_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


def _safe_port(value: object, default: int) -> int:
    try:
        port = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return port if 1 <= port <= 65535 else default
