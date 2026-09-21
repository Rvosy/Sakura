from dataclasses import dataclass


@dataclass(frozen=True)
class ScreenAwarenessSettings:
    enabled: bool = True
    check_interval_minutes: int = 20
    cooldown_minutes: int = 10
    screen_context_batch_limit: int = 6
    screen_context_resolution: str = "fullscreen"

    def normalized(self):
        return self

    def allows_screen_context(self):
        return self.enabled

    def values(self):
        return {"enabled": self.enabled, "checkIntervalMinutes": self.check_interval_minutes,
                "cooldownMinutes": self.cooldown_minutes, "batchLimit": self.screen_context_batch_limit,
                "resolution": self.screen_context_resolution}

    @classmethod
    def parse(cls, values):
        defaults = cls().values()
        settings = {key: values.get(key, value) for key, value in defaults.items()}
        if type(settings["enabled"]) is not bool:
            raise ValueError("SCREEN_AWARENESS_SETTINGS_INVALID")
        for key, maximum in (("checkIntervalMinutes", 120), ("cooldownMinutes", 120), ("batchLimit", 20)):
            if type(settings[key]) is not int or not 1 <= settings[key] <= maximum:
                raise ValueError("SCREEN_AWARENESS_SETTINGS_INVALID")
        if settings["resolution"] not in {"fullscreen", "720p", "1080p", "2160p"}:
            raise ValueError("SCREEN_AWARENESS_SETTINGS_INVALID")
        return cls(settings["enabled"], settings["checkIntervalMinutes"], settings["cooldownMinutes"],
                   settings["batchLimit"], settings["resolution"])


def settings_descriptor():
    return {"sectionId": "screen_awareness", "title": "主动屏幕感知", "order": 50, "presentation": {"component": "form", "alignedUnits": True},
            "fields": [
                {"key": "enabled", "label": "启用主动屏幕感知", "type": "boolean", "default": True,
                 "description": "定时截屏，判断是否主动搭话。"},
                {"key": "checkIntervalMinutes", "label": "截图检查间隔", "unit": "分钟", "type": "integer", "default": 20, "minimum": 1, "maximum": 120,
                 "enabledWhen": {"field": "enabled", "equals": "true"}},
                {"key": "cooldownMinutes", "label": "最短搭话间隔", "unit": "分钟", "type": "integer", "default": 10, "minimum": 1, "maximum": 120,
                 "enabledWhen": {"field": "enabled", "equals": "true"}},
                {"key": "batchLimit", "label": "单次最多发送截图", "unit": "张", "type": "integer", "default": 6, "minimum": 1, "maximum": 20,
                 "enabledWhen": {"field": "enabled", "equals": "true"}},
                {"key": "resolution", "label": "截图分辨率", "tooltip": "发送前按比例缩小，不放大截图", "type": "select", "default": "fullscreen",
                 "enabledWhen": {"field": "enabled", "equals": "true"},
                 "options": [{"value": value, "label": label} for value, label in
                             (("fullscreen", "全屏分辨率"), ("720p", "720p"), ("1080p", "1080p"), ("2160p", "2160p"))]},
            ]}
