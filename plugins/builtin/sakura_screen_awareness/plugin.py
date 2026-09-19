from __future__ import annotations

import threading
from time import monotonic

try:
    from .policy import ScreenAwarenessPolicy
    from .settings import ScreenAwarenessSettings, settings_descriptor
except ImportError:
    from policy import ScreenAwarenessPolicy
    from settings import ScreenAwarenessSettings, settings_descriptor


PROACTIVE_PROMPT = (
    "这是一次由 Sakura 定时截图触发的主动屏幕观察。以下截图按时间顺序展示我最近正在做的事情。"
    "请结合最近聊天历史和这些截图，以当前角色的语气自然接话：可以评论变化、接续任务、询问卡点或提供轻量帮助。"
    "不要逐张复述，也不要因为时间或久坐机械地提醒休息；如果没有明显变化，就简短说出你能确认的具体内容。"
)


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
                if self._logger is not None:
                    self._logger.warning("主动屏幕感知未完成", fields={"reasonCode": getattr(error, "code", "SCREEN_AWARENESS_FAILED")})
            self._stop.wait(10)

    def load_settings(self):
        return ScreenAwarenessSettings.parse(self._config.get()).values()

    def save_settings(self, values):
        settings = ScreenAwarenessSettings.parse(values)
        return {"applicationState": self._config.update(settings.values())}

    def apply_settings(self, values):
        settings = ScreenAwarenessSettings.parse(values)
        with self._lock:
            self._settings = settings
        self._clear(cancel=True)
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
            self._release(resources)
            return
        if plan["action"] == "capture":
            # Free the oldest frame before requesting another at the host quota.
            with self._lock:
                dropped = self._resources[:max(0, len(self._resources) - plan["batchLimit"] + 1)]
                self._resources = self._resources[len(dropped):]
            self._release(dropped)
            resource = self._screen.capture({"sessionId": facts["sessionId"], "resolution": plan["resolution"]})
            latest = self._chat.current()
            with self._lock:
                stale = (self._stop.is_set() or epoch != self._epoch or latest["sessionId"] != facts["sessionId"]
                         or latest["activityRevision"] != facts["activityRevision"] or not latest["idle"])
                stale = stale or latest.get("interactionRevision", 0) != facts.get("interactionRevision", 0)
                if not stale:
                    self._resources.append(resource["resourceId"])
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
