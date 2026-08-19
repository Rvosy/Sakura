from __future__ import annotations


class UmbrellaService:
    def __init__(self) -> None:
        self._weather_instance_id = ""
        self._raining = False
        self._event_count = 0

    def bind_weather(self, weather) -> None:
        current = weather.current()
        self._weather_instance_id = current["instanceId"]
        self._raining = bool(current["raining"])

    def weather_changed(self, payload) -> None:
        self._event_count += 1
        self._raining = bool(payload["raining"])

    def status(self) -> dict[str, object]:
        return {
            "weatherInstanceId": self._weather_instance_id,
            "raining": self._raining,
            "eventCount": self._event_count,
        }


class UmbrellaPlugin:
    def setup(self, context) -> None:
        service = UmbrellaService()

        def use_weather(weather, scope) -> None:
            service.bind_weather(weather)
            scope.on("com.example.weather.changed", service.weather_changed)

        context.inject("com.example.weather", use_weather)
        context.provide(
            "com.example.umbrella",
            service,
            exports=("status",),
        )
