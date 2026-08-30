from __future__ import annotations

from typing import Any

import pytest

from app.core_host.composer_tools import ComposerToolsBoundary
from app.core_host.plugin_host_services import HostServiceError, _ComposerToolsV0HostService


def test_composer_tool_host_service_projects_and_invokes_bounded_actions() -> None:
    callbacks: list[tuple[str, str, object]] = []

    def invoke(handle: str, shape: str, payload: object) -> dict[str, str]:
        callbacks.append((handle, shape, payload))
        return {"status": "completed", "message": "opened"}

    service = _ComposerToolsV0HostService(invoke)
    handle = f"cb_{'a' * 32}"
    registered = service.call(
        "register",
        [
            "com.example.tools",
            {
                "toolId": "browser",
                "label": "浏览器",
                "description": "打开受控浏览器",
                "icon": "globe",
                "order": 20,
            },
            handle,
        ],
    )

    assert service.snapshot() == [
        {
            "id": "com.example.tools:browser",
            "pluginId": "com.example.tools",
            "toolId": "browser",
            "label": "浏览器",
            "description": "打开受控浏览器",
            "icon": "globe",
            "order": 20.0,
        }
    ]
    assert service.invoke("com.example.tools:browser") == {
        "status": "completed",
        "message": "opened",
    }
    assert callbacks == [
        (handle, "ui.composer_tool.invoke", {"source": "composer"})
    ]
    assert service.call("unregister", [registered["registrationId"]]) == {"removed": True}
    assert service.snapshot() == []


def test_composer_tool_host_service_rejects_untrusted_rendering_data() -> None:
    service = _ComposerToolsV0HostService(lambda *_args: None)
    with pytest.raises(HostServiceError, match="COMPOSER_TOOL_DESCRIPTOR_INVALID"):
        service.call(
            "register",
            [
                "com.example.tools",
                {"toolId": "unsafe", "label": "Unsafe", "icon": "<svg>"},
                f"cb_{'b' * 32}",
            ],
        )


class _Application:
    def __init__(self) -> None:
        self.invoked: list[str] = []

    def composer_tools(self) -> list[dict[str, Any]]:
        return [{
            "id": "com.example.tools:browser",
            "pluginId": "com.example.tools",
            "toolId": "browser",
            "label": "浏览器",
            "description": "打开受控浏览器",
            "icon": "globe",
            "order": 20.0,
        }]

    def invoke_composer_tool(self, tool_id: str) -> dict[str, str]:
        self.invoked.append(tool_id)
        return {"status": "completed", "message": ""}


def _request(name: str, payload: dict[str, object]) -> dict[str, object]:
    return {
        "protocolMajor": 2,
        "protocolMinor": 2,
        "kind": "request",
        "generationId": "generation-a",
        "generationCredential": "credential-a",
        "id": "request-a",
        "name": name,
        "payload": payload,
    }


def test_composer_tools_boundary_keeps_generation_identity_and_opaque_ids() -> None:
    application = _Application()
    boundary = ComposerToolsBoundary(
        "generation-a",
        "credential-a",
        application_provider=lambda: application,
    )

    snapshot = boundary.handle(_request("ui.composer_tools.get", {}))
    assert snapshot["ok"] is True
    assert snapshot["payload"]["coreGenerationId"] == "generation-a"
    assert snapshot["payload"]["tools"][0]["id"] == "com.example.tools:browser"

    invoked = boundary.handle(
        _request("ui.composer_tools.invoke", {"toolId": "com.example.tools:browser"})
    )
    assert invoked["ok"] is True
    assert application.invoked == ["com.example.tools:browser"]

    invalid = boundary.handle(
        _request("ui.composer_tools.invoke", {"toolId": "../private"})
    )
    assert invalid["ok"] is False
    assert invalid["error"]["code"] == "COMPOSER_TOOL_ID_INVALID"
