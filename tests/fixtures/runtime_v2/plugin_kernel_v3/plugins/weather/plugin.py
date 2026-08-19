from __future__ import annotations

import secrets


class WeatherService:
    def __init__(self, context) -> None:
        self._context = context
        self._instance_id = secrets.token_hex(8)
        self._raining = False

    def current(self) -> dict[str, object]:
        return {
            "instanceId": self._instance_id,
            "raining": self._raining,
        }

    def set_raining(self, raining: bool) -> dict[str, object]:
        self._raining = bool(raining)
        payload = self.current()
        self._context.emit("com.example.weather.changed", payload)
        return payload


class WeatherPlugin:
    def setup(self, context) -> None:
        context.provide(
            "com.example.weather",
            WeatherService(context),
            exports=("current", "set_raining"),
        )
