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
    return {"sectionId": "screen_awareness", "title": "主动屏幕感知", "order": 50,
            "fields": [
                {"key": "enabled", "label": "启用主动屏幕感知", "type": "boolean", "default": True,
                 "description": "定时截取光标所在屏幕，并发送给当前对话模型。"},
                {"key": "checkIntervalMinutes", "label": "截图间隔（分钟）", "type": "integer", "default": 20, "minimum": 1, "maximum": 120},
                {"key": "cooldownMinutes", "label": "收集时长（分钟）", "type": "integer", "default": 10, "minimum": 1, "maximum": 120},
                {"key": "batchLimit", "label": "最多保留截图", "type": "integer", "default": 6, "minimum": 1, "maximum": 20},
                {"key": "resolution", "label": "截图分辨率", "type": "select", "default": "fullscreen",
                 "options": [{"value": value, "label": label} for value, label in
                             (("fullscreen", "原始分辨率"), ("720p", "720p"), ("1080p", "1080p"), ("2160p", "2160p"))]},
            ]}
