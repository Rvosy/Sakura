from __future__ import annotations

from app.core.runtime_resources import ResourceRegistry, ResourceState


def test_qt_free_registry_owns_and_stops_services() -> None:
    registry = ResourceRegistry()
    stopped: list[str] = []
    resource = registry.track_service(stop=lambda: stopped.append("service"))

    assert resource.health() is ResourceState.READY
    registry.stop_all()

    assert stopped == ["service"]
    assert resource.health() is ResourceState.STOPPED
    assert registry._resources == []
