from __future__ import annotations

import threading
from time import monotonic

try:
    from .policy import ScreenAwarenessPolicy
    from .prompts import PROACTIVE_PROMPT
    from .settings import ScreenAwarenessSettings, settings_descriptor
except ImportError:
    from policy import ScreenAwarenessPolicy
    from prompts import PROACTIVE_PROMPT
    from settings import ScreenAwarenessSettings, settings_descriptor


class ScreenAwarenessRuntime:
    def __init__(self, screen, chat, config, *, clock=monotonic, logger=None):
        self._screen, self._chat, self._config = screen, chat, config
        self._logger = logger
        self._settings = ScreenAwarenessSettings.parse(config.get())
        self._policy = ScreenAwarenessPolicy(self._settings, clock=clock)
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._worker = None
        self._epoch = 0
        self._resources = []
        self._activity = None
        self._interaction = None
        self._operation = None

    def start(self):
        self._worker = threading.Thread(target=self._run, name="screen-awareness", daemon=True)
        self._worker.start()

    def _run(self):
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as error:
                self._clear()
                self._log("warning", "查看屏幕内容时出错了",
                          fields={"event": "screen.awareness.failed", "reason_code": getattr(error, "code", "SCREEN_AWARENESS_FAILED")})
            self._stop.wait(10)

    def _log(self, severity, message, *, facts=None, fields=None):
        if self._logger is not None:
            if facts is not None:
                message = f"[{facts['characterName']}] {message}"
            getattr(self._logger, severity)(message, fields=fields)

    def load_settings(self):
        return ScreenAwarenessSettings.parse(self._config.get()).values()

    def save_settings(self, values):
        settings = ScreenAwarenessSettings.parse(values)
        return {"applicationState": self._config.update(settings.values())}

    def apply_settings(self, values):
        settings = ScreenAwarenessSettings.parse(values)
        with self._lock:
            was_enabled = self._settings.enabled
            self._settings = settings
        count = self._clear(cancel=True)
        if was_enabled != settings.enabled:
            self._log("info", "已恢复主动看屏幕" if settings.enabled else "已停止主动看屏幕",
                      fields={"event": "screen.awareness.enabled" if settings.enabled else "screen.awareness.disabled",
                              "screen_cleared_count": count})
        elif count:
            self._log("info", "设置已更新，之前的截图已清空",
                      fields={"event": "screen.awareness.cleared", "screen_cleared_count": count, "screen_note": "设置变化"})
        return "applied"

    def _release(self, resources):
        for resource in resources:
            try:
                self._screen.release(resource)
            except Exception:
                # Scope/session revocation already releases the host resources.
                pass

    def _clear(self, *, cancel=False):
        with self._lock:
            self._epoch += 1
            resources, self._resources = self._resources, []
            self._policy.reset(self._settings)
            operation = self._operation if cancel else None
            if cancel:
                self._operation = None
        self._release(resources)
        if operation is not None:
            self._chat.cancel(operation)
        return len(resources)

    def tick(self):
        if self._stop.is_set():
            return
        facts = self._chat.current()
        with self._lock:
            if self._stop.is_set():
                return
            activity = self._activity != facts["activityRevision"]
            self._activity = facts["activityRevision"]
            interaction = facts.get("interactionRevision", 0)
            if self._interaction is not None and self._interaction != interaction:
                self._epoch += 1
                self._policy.reset(self._settings)
            self._interaction = interaction
            current = {"sessionId": facts["sessionId"], "idle": facts["idle"], "activity": activity}
            plan = self._policy.step(current)
            epoch = self._epoch
        if plan["action"] == "clear":
            with self._lock:
                resources, self._resources = self._resources, []
                if resources:
                    if plan["sessionChanged"]:
                        message = "已切换角色，之前的截图已清空" if facts["sessionId"] else "当前角色不可用，之前的截图已清空"
                    else:
                        message = "已开始新的聊天，之前的截图已清空"
                    self._log("info", message, fields={"event": "screen.awareness.cleared", "screen_cleared_count": len(resources)})
            self._release(resources)
            return
        if plan["action"] == "capture":
            # Free the oldest frame before requesting another at the host quota.
            with self._lock:
                dropped = self._resources[:max(0, len(self._resources) - plan["batchLimit"] + 1)]
                self._resources = self._resources[len(dropped):]
            self._release(dropped)
            try:
                resource = self._screen.capture({"sessionId": facts["sessionId"], "resolution": plan["resolution"]})
            except Exception as error:
                self._log("warning", "这次没能看到屏幕", facts=facts,
                          fields={"event": "screen.awareness.capture_failed", "reason_code": getattr(error, "code", "SCREEN_CAPTURE_FAILED")})
                self._clear()
                return
            latest = self._chat.current()
            with self._lock:
                stale = (self._stop.is_set() or epoch != self._epoch or latest["sessionId"] != facts["sessionId"]
                         or latest["activityRevision"] != facts["activityRevision"] or not latest["idle"])
                stale = stale or latest.get("interactionRevision", 0) != facts.get("interactionRevision", 0)
                if not stale:
                    self._resources.append(resource["resourceId"])
                    fields = {"event": "screen.awareness.captured", "screen_count": len(self._resources),
                              "screen_limit": plan["batchLimit"], "screen_captured_at": resource["capturedAt"]}
                    if dropped:
                        fields["screen_note"] = "已替换最早的一张截图"
                    self._log("info", f"看了一眼屏幕（{len(self._resources)}/{plan['batchLimit']}）",
                              facts=facts, fields=fields)
                    plan = self._policy.step({**current, "count": len(self._resources), "revision": plan["revision"]})
            if stale:
                self._release([resource["resourceId"]])
                return
        if plan["action"] == "submit":
            with self._lock:
                if epoch != self._epoch or self._stop.is_set():
                    return
                resources = list(self._resources)
            try:
                result = self._chat.submit({"sessionId": facts["sessionId"], "message": PROACTIVE_PROMPT, "resources": resources})
                if result.get("accepted"):
                    with self._lock:
                        stale = epoch != self._epoch or self._stop.is_set()
                        if not stale:
                            self._operation = result["operationId"]
                    if stale:
                        self._chat.cancel(result["operationId"])
                else:
                    reason = result["reasonCode"]
                    message = {"CHAT_BUSY": "正在聊天，这次先跳过",
                               "CHAT_SESSION_STALE": "已切换角色，这次先跳过",
                               "CHAT_ADMISSION_EXPIRED": "当前互动已变化，这次先跳过"}.get(reason)
                    self._log("info" if message else "warning", message or "这次没能查看屏幕内容", facts=facts,
                              fields={"event": "screen.awareness.skipped", "reason_code": reason, "screen_count": len(resources)})
            except Exception as error:
                self._log("warning", "查看屏幕内容时出错了", facts=facts,
                          fields={"event": "screen.awareness.submit_failed", "reason_code": getattr(error, "code", "SCREEN_AWARENESS_FAILED")})
            finally:
                self._clear()

    def close(self):
        self._stop.set()
        self._clear(cancel=True)
        if self._worker is not None and self._worker is not threading.current_thread():
            self._worker.join(timeout=0.5)


class ScreenAwarenessPlugin:
    def setup(self, context):
        from sakura_screen import ScreenClient

        screen = ScreenClient(context.get("sakura.host.screen"))
        context.effect(screen.close)
        runtime = ScreenAwarenessRuntime(screen, context.get("sakura.host.chat"),
                                         context.config, logger=context.get("sakura.host.logging"))
        context.effect(runtime.close)
        context.config.on_change(runtime.apply_settings)
        context.get("sakura.host.settings").register(settings_descriptor(), load=runtime.load_settings, save=runtime.save_settings)
        context.get("sakura.host.settings").place("screen_awareness", page_id="host:interaction", order=50)
        runtime.start()
