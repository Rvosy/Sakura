from __future__ import annotations

import copy
from pathlib import Path

import pytest

from app.core_host.server import (
    CAPABILITIES,
    ControlDispatcher,
    HostConfig,
    TransportFailure,
)


GENERATION_ID = "00000000-0000-4000-8000-000000001c03"
CREDENTIAL = "44" * 16
APP_ROOT = Path("/isolated/not-read/core-host-negotiation")


def request(name: str, payload: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "protocolMajor": 2,
        "protocolMinor": 1,
        "kind": "request",
        "generationId": GENERATION_ID,
        "generationCredential": CREDENTIAL,
        "id": name,
        "name": name,
        "payload": payload or {},
        "deadlineMs": 3000,
        "priority": "control",
    }


def hello_payload(*, major: int = 2, minimum: int = 0, maximum: int = 1) -> dict[str, object]:
    return {
        "protocol": {"major": major, "minMinor": minimum, "maxMinor": maximum},
        "requiredCapabilities": list(CAPABILITIES),
        "optionalCapabilities": ["future.optional"],
    }


def test_exact_and_downward_minor_negotiation_are_deterministic() -> None:
    for maximum, expected in [(1, 1), (0, 0)]:
        dispatcher = ControlDispatcher(HostConfig(APP_ROOT, GENERATION_ID, CREDENTIAL))
        try:
            response, _ = dispatcher.dispatch(
                request("system.hello", hello_payload(maximum=maximum))
            )
            assert response["ok"] is True
            assert response["protocolMinor"] == expected
            assert response["payload"]["negotiated"] == {
                "major": 2,
                "minor": expected,
                "capabilities": list(CAPABILITIES),
            }
        finally:
            dispatcher.close()


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda payload: payload["protocol"].update(major=3), "PROTOCOL_MAJOR_MISMATCH"),
        (
            lambda payload: payload["requiredCapabilities"].append("missing.required"),
            "CAPABILITY_NEGOTIATION_FAILED",
        ),
        (
            lambda payload: payload["requiredCapabilities"].append(CAPABILITIES[0]),
            "INVALID_NEGOTIATION",
        ),
        (lambda payload: payload["protocol"].update(maxMinor=True), "INVALID_NEGOTIATION"),
        (lambda payload: payload.update(optionalCapabilities="invalid"), "INVALID_NEGOTIATION"),
    ],
)
def test_incompatible_or_invalid_hello_fails_closed(mutate, code: str) -> None:
    dispatcher = ControlDispatcher(HostConfig(APP_ROOT, GENERATION_ID, CREDENTIAL))
    try:
        payload = hello_payload()
        mutate(payload)
        response, _ = dispatcher.dispatch(request("system.hello", payload))
        assert response["ok"] is False
        assert response["error"]["code"] == code
        initialize, _ = dispatcher.dispatch(request("core.initialize", {}))
        assert initialize["error"]["code"] == "HANDSHAKE_FAILED"
    finally:
        dispatcher.close()


def test_hello_order_and_duplicate_are_rejected() -> None:
    dispatcher = ControlDispatcher(HostConfig(APP_ROOT, GENERATION_ID, CREDENTIAL))
    try:
        early, _ = dispatcher.dispatch(request("system.health"))
        assert early["error"]["code"] == "HANDSHAKE_REQUIRED"
        hello, _ = dispatcher.dispatch(request("system.hello", hello_payload()))
        assert hello["ok"] is True
        duplicate, _ = dispatcher.dispatch(request("system.hello", hello_payload()))
        assert duplicate["error"]["code"] == "HANDSHAKE_ALREADY_COMPLETE"
    finally:
        dispatcher.close()


def test_shutdown_during_handshake_is_classified_and_stops_the_host() -> None:
    dispatcher = ControlDispatcher(HostConfig(APP_ROOT, GENERATION_ID, CREDENTIAL))
    try:
        response, should_stop = dispatcher.dispatch(request("system.shutdown"))
        assert response["error"]["code"] == "SHUTDOWN_DURING_HANDSHAKE"
        assert should_stop is True
    finally:
        dispatcher.close()


@pytest.mark.parametrize("credential", [None, "55" * 16, ""])
def test_missing_wrong_and_stale_credentials_are_transport_fatal(credential: str | None) -> None:
    dispatcher = ControlDispatcher(HostConfig(APP_ROOT, GENERATION_ID, CREDENTIAL))
    message = request("system.hello", hello_payload())
    if credential is None:
        del message["generationCredential"]
    else:
        message["generationCredential"] = credential
    try:
        with pytest.raises(TransportFailure) as raised:
            dispatcher.dispatch(message)
        assert raised.value.code == "GENERATION_CREDENTIAL_MISMATCH"
        assert CREDENTIAL not in str(raised.value)
        supplied = message.get("generationCredential")
        if supplied:
            assert str(supplied) not in str(raised.value)
    finally:
        dispatcher.close()


def test_replayed_response_cannot_change_the_active_generation() -> None:
    dispatcher = ControlDispatcher(HostConfig(APP_ROOT, GENERATION_ID, CREDENTIAL))
    old = request("system.hello", hello_payload())
    old["generationCredential"] = "66" * 16
    replay = copy.deepcopy(old)
    try:
        with pytest.raises(TransportFailure):
            dispatcher.dispatch(replay)
    finally:
        dispatcher.close()
