from __future__ import annotations

import time

import pytest

from app.core_host.server import ControlDispatcher, HostConfig


GENERATION_ID = "00000000-0000-4000-8000-000000001c02"
GENERATION_CREDENTIAL = "22" * 16
CAPABILITIES = [
    "system.hello",
    "system.health",
    "system.shutdown",
    "core.initialize",
    "core.snapshot",
]


def request(
    request_id: str,
    name: str,
    payload: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "protocolMajor": 2,
        "protocolMinor": 1,
        "kind": "request",
        "generationId": GENERATION_ID,
        "generationCredential": GENERATION_CREDENTIAL,
        "id": request_id,
        "name": name,
        "payload": payload if payload is not None else (
            {
                "protocol": {"major": 2, "minMinor": 0, "maxMinor": 1},
                "requiredCapabilities": CAPABILITIES,
                "optionalCapabilities": [],
            }
            if name == "system.hello"
            else {}
        ),
        "deadlineMs": 3000,
        "priority": "control",
    }


def wait_for_readiness(
    dispatcher: ControlDispatcher,
    expected: str,
    timeout: float = 1.0,
) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot, _ = dispatcher.dispatch(request("snapshot", "core.snapshot"))
        payload = snapshot["payload"]
        if payload["readiness"] == expected:
            return payload
        time.sleep(0.005)
    raise AssertionError(f"readiness did not become {expected}")


def negotiated_dispatcher(generation_number: int = 1) -> ControlDispatcher:
    dispatcher = ControlDispatcher(
        HostConfig(GENERATION_ID, GENERATION_CREDENTIAL, generation_number=generation_number)
    )
    hello, _ = dispatcher.dispatch(request("hello", "system.hello"))
    assert hello["ok"] is True
    return dispatcher


def test_hello_precedes_initialize_and_python_builds_the_minimal_snapshot() -> None:
    dispatcher = negotiated_dispatcher(generation_number=7)
    try:
        started = time.monotonic()
        initialize, _ = dispatcher.dispatch(
            request("initialize", "core.initialize", {"mode": "ready", "delayMs": 20})
        )
        assert time.monotonic() - started < 0.2
        assert initialize["ok"] is True
        assert initialize["payload"] == {
            "accepted": True,
            "alreadyStarted": False,
            "readiness": "initializing",
        }

        snapshot = wait_for_readiness(dispatcher, "ready")
        assert snapshot == {
            "activeInteractionSummary": None,
            "capabilities": [
                "system.hello",
                "system.health",
                "system.shutdown",
                "core.initialize",
                "core.snapshot",
            ],
            "components": {"fixture": {"state": "ready"}},
            "coreConfigRevision": 0,
            "currentCharacterSummary": None,
            "generationId": GENERATION_ID,
            "generationNumber": 7,
            "readiness": "ready",
            "revision": 2,
            "schemaVersion": 1,
        }
    finally:
        dispatcher.close()


@pytest.mark.parametrize("mode", ["setup_required", "degraded", "failed"])
def test_fake_initialization_reports_each_stable_readiness(mode: str) -> None:
    dispatcher = negotiated_dispatcher()
    try:
        response, _ = dispatcher.dispatch(
            request("initialize", "core.initialize", {"mode": mode})
        )
        assert response["payload"]["readiness"] == "initializing"
        snapshot = wait_for_readiness(dispatcher, mode)
        assert snapshot["components"]["fixture"]["state"] == mode
        assert snapshot["revision"] == 2
    finally:
        dispatcher.close()


def test_hung_initialization_does_not_block_health_or_shutdown() -> None:
    dispatcher = negotiated_dispatcher()
    try:
        initialize, _ = dispatcher.dispatch(
            request("initialize", "core.initialize", {"mode": "hang"})
        )
        assert initialize["payload"]["readiness"] == "initializing"

        for index in range(5):
            started = time.monotonic()
            health, _ = dispatcher.dispatch(request(f"health-{index}", "system.health"))
            assert time.monotonic() - started < 0.2
            assert health["payload"]["hostState"] == "initializing"
            assert health["payload"]["status"] == "healthy"

        repeated, _ = dispatcher.dispatch(
            request("initialize-again", "core.initialize", {"mode": "ready"})
        )
        assert repeated["payload"] == {
            "accepted": True,
            "alreadyStarted": True,
            "readiness": "initializing",
        }
        shutdown, should_stop = dispatcher.dispatch(
            request("shutdown", "system.shutdown")
        )
        assert shutdown["payload"] == {"accepted": True}
        assert should_stop is True
    finally:
        started = time.monotonic()
        dispatcher.close()
        assert time.monotonic() - started < 1.0


@pytest.mark.parametrize(
    "payload",
    [
        {"mode": "unknown"},
        {"mode": "ready", "delayMs": True},
        {"mode": "ready", "delayMs": -1},
        {"unexpected": "field"},
    ],
)
def test_invalid_initialize_payload_is_rejected_without_starting(payload: dict[str, object]) -> None:
    dispatcher = negotiated_dispatcher()
    try:
        rejected, _ = dispatcher.dispatch(
            request("initialize", "core.initialize", payload)
        )
        assert rejected["ok"] is False
        assert rejected["error"]["code"] == "INVALID_INITIALIZE"
        snapshot, _ = dispatcher.dispatch(request("snapshot", "core.snapshot"))
        assert snapshot["payload"]["readiness"] == "transport_ready"
        assert snapshot["payload"]["revision"] == 0
    finally:
        dispatcher.close()

