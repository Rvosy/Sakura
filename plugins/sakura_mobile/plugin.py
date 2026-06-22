from __future__ import annotations

import threading
from typing import Any

from app.plugins import PluginBase, PluginCapabilityRegistry, PluginContext
from app.plugins import SettingsPanelContribution

from plugins.sakura_mobile.server import DEFAULT_HOST, DEFAULT_PORT, mobile_access_urls, run_mobile_server


class SakuraMobilePlugin(PluginBase):
    plugin_id = "sakura_mobile"
    plugin_version = "1.0.0"

    def __init__(self) -> None:
        self._context: PluginContext | None = None
        self._server: Any | None = None
        self._thread: threading.Thread | None = None
        self._cleanup_registered = False
        self._last_error = ""

    def initialize(
        self,
        register: PluginCapabilityRegistry,
        context: PluginContext,
    ) -> None:
        self._context = context
        register.register_settings_panel(
            SettingsPanelContribution(
                section_id="sakura_mobile",
                title="手机端",
                build=lambda parent=None: _build_settings_panel(self, parent),
                order=70.0,
            )
        )
        resources = getattr(getattr(context, "services", None), "resources", None)
        register_cleanup = getattr(resources, "register_cleanup", None)
        if callable(register_cleanup):
            register_cleanup(self.stop, label="mobile_server", shutdown_order=640)
            self._cleanup_registered = True

    def on_app_start(self, _event: Any) -> None:
        self.start()

    def shutdown(self) -> None:
        if not self._cleanup_registered:
            self.stop()

    def config(self) -> dict[str, Any]:
        context = self._require_context()
        config = context.get_config()
        return {
            "enabled": _as_bool(config.get("enabled"), False),
            "host": str(config.get("host") or DEFAULT_HOST).strip() or DEFAULT_HOST,
            "port": _safe_port(config.get("port"), DEFAULT_PORT),
            "token": str(config.get("token") or "sakura").strip() or "sakura",
        }

    def save_config(self, config: dict[str, Any]) -> None:
        context = self._require_context()
        context.save_config(config)
        self.restart()

    def start(self) -> None:
        if self._server is not None:
            return
        context = self._require_context()
        config = self.config()
        if not config["enabled"]:
            self._last_error = ""
            context.log("手机端插件已禁用")
            return
        mobile_service = getattr(getattr(context, "services", None), "mobile", None)
        if mobile_service is None:
            self._last_error = "宿主桥接未就绪"
            context.log("手机端服务启动失败：宿主桥接未就绪")
            return
        try:
            server = run_mobile_server(
                context.base_dir,
                mobile_service,
                host=str(config["host"]),
                port=int(config["port"]),
                token=str(config["token"]),
            )
        except OSError as exc:
            self._last_error = str(exc)
            context.log("手机端服务启动失败", {"error": str(exc)})
            return
        thread = threading.Thread(
            target=server.serve_forever,
            name="SakuraMobilePlugin",
            daemon=True,
        )
        thread.start()
        self._server = server
        self._thread = thread
        self._last_error = ""
        context.log(
            "手机端服务已启动",
            {"host": config["host"], "port": config["port"]},
        )

    def stop(self) -> None:
        server = self._server
        self._server = None
        self._thread = None
        if server is None:
            return
        try:
            server.shutdown()
            server.server_close()
        except OSError:
            pass

    def restart(self) -> None:
        self.stop()
        self.start()

    def status(self) -> dict[str, Any]:
        config = self.config()
        return {
            **config,
            "running": self._server is not None,
            "error": self._last_error,
            **mobile_access_urls(str(config["host"]), int(config["port"]), str(config["token"])),
        }

    def _require_context(self) -> PluginContext:
        if self._context is None:
            raise RuntimeError("手机端插件尚未初始化。")
        return self._context


def _build_settings_panel(plugin: SakuraMobilePlugin, parent: Any = None) -> Any:
    from plugins.sakura_mobile.settings_panel import SakuraMobileSettingsPanel

    return SakuraMobileSettingsPanel(plugin, parent)


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
