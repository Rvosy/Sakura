from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from app.core_host.assistant_adapter import ReadinessResult
from app.core_host.server import ControlDispatcher, HostConfig, InitializeError, ReadinessController


GENERATION_ID = "00000000-0000-4000-8000-000000001c02"
GENERATION_CREDENTIAL = "22" * 16
CAPABILITIES = [
    "system.hello",
    "system.health",
    "system.shutdown",
    "core.initialize",
    "core.snapshot",
]
SUMMARY = {
    "id": "sakura",
    "displayName": "Sakura",
    "initialMessage": "Hello",
    "replyTones": ["warm"],
    "portraitChoices": ["default"],
}


def readiness_result(
    state: str = "ready",
    *,
    code: str = "READY",
    summary: dict[str, object] | None = SUMMARY,
) -> ReadinessResult:
    return ReadinessResult(
        state=state,  # type: ignore[arg-type]
        code=code,
        message="sanitized",
        retryable=False,
        current_character_summary=summary,
    )


class FakeInitializer:
    def __init__(
        self,
        result: ReadinessResult | None = None,
        *,
        entered: threading.Event | None = None,
        release: threading.Event | None = None,
        error: BaseException | None = None,
        close_error: BaseException | None = None,
        close_release: threading.Event | None = None,
    ) -> None:
        self.result = result if result is not None else readiness_result()
        self.entered = entered
        self.release = release
        self.error = error
        self.close_error = close_error
        self.close_release = close_release
        self.cancel: threading.Event | None = None
        self.initialize_calls = 0
        self.close_calls = 0
        self.closed = threading.Event()

    def initialize(self, cancel: threading.Event) -> ReadinessResult:
        self.initialize_calls += 1
        self.cancel = cancel
        if self.entered is not None:
            self.entered.set()
        if self.release is not None:
            self.release.wait(2)
        if self.error is not None:
            raise self.error
        return self.result

    def close(self) -> None:
        self.close_calls += 1
        self.closed.set()
        if self.close_release is not None:
            self.close_release.wait(2)
        if self.close_error is not None:
            raise self.close_error


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


def config(root: Path, generation_number: int = 1, generation_id: str = GENERATION_ID) -> HostConfig:
    return HostConfig(
        root,
        generation_id,
        GENERATION_CREDENTIAL,
        generation_number=generation_number,
    )


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


def negotiated_dispatcher(
    root: Path,
    factory: Callable[[Path], FakeInitializer],
    generation_number: int = 1,
) -> ControlDispatcher:
    dispatcher = ControlDispatcher(
        config(root, generation_number),
        initializer_factory=factory,
    )
    hello, _ = dispatcher.dispatch(request("hello", "system.hello"))
    assert hello["ok"] is True
    return dispatcher


def test_empty_initialize_is_single_start_and_publishes_exact_atomic_snapshots(
    tmp_path: Path,
) -> None:
    initializer = FakeInitializer()
    roots: list[Path] = []
    dispatcher = negotiated_dispatcher(
        tmp_path,
        lambda root: roots.append(root) or initializer,
        generation_number=7,
    )
    try:
        before, _ = dispatcher.dispatch(request("before", "core.snapshot"))
        assert before["payload"] == {
            "activeInteractionSummary": None,
            "capabilities": CAPABILITIES,
            "components": {},
            "coreConfigRevision": 0,
            "currentCharacterSummary": None,
            "generationId": GENERATION_ID,
            "generationNumber": 7,
            "readiness": "transport_ready",
            "revision": 0,
            "schemaVersion": 1,
        }

        started = time.monotonic()
        initialize, _ = dispatcher.dispatch(request("initialize", "core.initialize", {}))
        assert time.monotonic() - started < 0.2
        assert initialize["payload"] == {
            "accepted": True,
            "alreadyStarted": False,
            "readiness": "initializing",
        }

        repeated, _ = dispatcher.dispatch(request("initialize-again", "core.initialize", {}))
        assert repeated["payload"] == {
            "accepted": True,
            "alreadyStarted": True,
            "readiness": repeated["payload"]["readiness"],
        }
        snapshot = wait_for_readiness(dispatcher, "ready")
        assert snapshot == {
            "activeInteractionSummary": None,
            "capabilities": CAPABILITIES,
            "components": {
                "assistant": {"state": "ready", "code": "READY", "retryable": False}
            },
            "coreConfigRevision": 0,
            "currentCharacterSummary": SUMMARY,
            "generationId": GENERATION_ID,
            "generationNumber": 7,
            "readiness": "ready",
            "revision": 2,
            "schemaVersion": 1,
        }
        assert roots == [tmp_path]
        assert initializer.initialize_calls == 1
    finally:
        dispatcher.close()
    assert initializer.close_calls == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"mode": "ready"},
        {"mode": "unknown"},
        {"delayMs": 1},
        {"unexpected": "field"},
    ],
)
def test_production_initialize_rejects_every_nonempty_payload_without_starting(
    tmp_path: Path,
    payload: dict[str, object],
) -> None:
    created: list[FakeInitializer] = []
    dispatcher = negotiated_dispatcher(
        tmp_path,
        lambda _root: created.append(FakeInitializer()) or created[-1],
    )
    try:
        rejected, _ = dispatcher.dispatch(request("initialize", "core.initialize", payload))
        assert rejected["ok"] is False
        assert rejected["error"]["code"] == "INVALID_INITIALIZE"
        snapshot, _ = dispatcher.dispatch(request("snapshot", "core.snapshot"))
        assert snapshot["payload"]["readiness"] == "transport_ready"
        assert snapshot["payload"]["revision"] == 0
        assert created == []
    finally:
        dispatcher.close()


@pytest.mark.parametrize(
    ("state", "code"),
    [
        ("setup_required", "CORE_CONFIG_SETUP_REQUIRED"),
        ("degraded", "CHARACTER_FALLBACK_APPLIED"),
        ("failed", "CONFIG_DATA_INVALID"),
    ],
)
def test_injected_initializer_reports_each_stable_readiness(
    tmp_path: Path,
    state: str,
    code: str,
) -> None:
    initializer = FakeInitializer(readiness_result(state, code=code, summary=None))
    dispatcher = negotiated_dispatcher(tmp_path, lambda _root: initializer)
    try:
        response, _ = dispatcher.dispatch(request("initialize", "core.initialize", {}))
        assert response["payload"]["readiness"] == "initializing"
        snapshot = wait_for_readiness(dispatcher, state)
        assert snapshot["components"] == {
            "assistant": {"state": state, "code": code, "retryable": False}
        }
        assert snapshot["revision"] == 2
    finally:
        dispatcher.close()


def test_slow_initializer_does_not_block_health_duplicate_or_shutdown(tmp_path: Path) -> None:
    entered = threading.Event()
    release = threading.Event()
    initializer = FakeInitializer(entered=entered, release=release)
    dispatcher = negotiated_dispatcher(tmp_path, lambda _root: initializer)
    dispatcher.dispatch(request("initialize", "core.initialize", {}))
    assert entered.wait(1)
    try:
        initializing, _ = dispatcher.dispatch(request("initializing", "core.snapshot"))
        assert initializing["payload"]["revision"] == 1
        assert initializing["payload"]["readiness"] == "initializing"
        assert initializing["payload"]["components"] == {
            "assistant": {
                "state": "initializing",
                "code": "INITIALIZING",
                "retryable": False,
            }
        }
        assert initializing["payload"]["currentCharacterSummary"] is None
        for index in range(5):
            started = time.monotonic()
            health, _ = dispatcher.dispatch(request(f"health-{index}", "system.health"))
            assert time.monotonic() - started < 0.2
            assert health["payload"] == {"hostState": "initializing", "status": "healthy"}

        repeated, _ = dispatcher.dispatch(request("again", "core.initialize", {}))
        assert repeated["payload"]["alreadyStarted"] is True
        shutdown, should_stop = dispatcher.dispatch(request("shutdown", "system.shutdown"))
        assert shutdown["payload"] == {"accepted": True}
        assert should_stop is True
    finally:
        release.set()
        dispatcher.close()
    assert initializer.cancel is not None and initializer.cancel.is_set()
    assert initializer.close_calls == 1


def test_shutdown_before_initialize_never_constructs_initializer(tmp_path: Path) -> None:
    created: list[FakeInitializer] = []
    controller = ReadinessController(
        config(tmp_path),
        initializer_factory=lambda _root: created.append(FakeInitializer()) or created[-1],
    )
    controller.close()
    controller.close()

    with pytest.raises(InitializeError, match="shutting down"):
        controller.begin({})
    assert created == []
    assert controller.snapshot()["revision"] == 0


@pytest.mark.parametrize("failure_at", ["factory", "initialize"])
def test_initializer_failure_publishes_sanitized_failed_result(
    tmp_path: Path,
    failure_at: str,
) -> None:
    secret = "PRIVATE_FAILURE_VALUE"
    if failure_at == "factory":
        def factory(_root: Path) -> FakeInitializer:
            raise RuntimeError(secret)
    else:
        initializer = FakeInitializer(error=RuntimeError(secret))
        factory = lambda _root: initializer
    dispatcher = negotiated_dispatcher(tmp_path, factory)
    try:
        dispatcher.dispatch(request("initialize", "core.initialize", {}))
        snapshot = wait_for_readiness(dispatcher, "failed")
        assert snapshot["components"] == {
            "assistant": {
                "state": "failed",
                "code": "ASSISTANT_INITIALIZATION_FAILED",
                "retryable": False,
            }
        }
        assert secret not in repr(snapshot)
    finally:
        dispatcher.close()


def test_close_throw_still_joins_worker_and_is_not_repeated(tmp_path: Path) -> None:
    close_error = RuntimeError("private close value")
    initializer = FakeInitializer(close_error=close_error)
    controller = ReadinessController(config(tmp_path), initializer_factory=lambda _root: initializer)
    controller.begin({})
    deadline = time.monotonic() + 1
    while controller.snapshot()["readiness"] == "initializing":
        assert time.monotonic() < deadline

    with pytest.raises(RuntimeError) as raised:
        controller.close()
    assert raised.value is close_error
    controller.close()
    assert initializer.close_calls == 1


def test_blocked_close_observes_cancel_and_has_single_owner(tmp_path: Path) -> None:
    close_release = threading.Event()
    initializer = FakeInitializer(close_release=close_release)
    controller = ReadinessController(config(tmp_path), initializer_factory=lambda _root: initializer)
    controller.begin({})
    deadline = time.monotonic() + 1
    while controller.snapshot()["readiness"] == "initializing":
        assert time.monotonic() < deadline

    closer = threading.Thread(target=controller.close)
    closer.start()
    assert initializer.closed.wait(1)
    assert initializer.cancel is not None and initializer.cancel.is_set()
    assert closer.is_alive()
    close_release.set()
    closer.join(1)
    assert not closer.is_alive()
    assert initializer.close_calls == 1


def test_late_old_generation_result_is_closed_and_never_published(tmp_path: Path) -> None:
    old_entered = threading.Event()
    old_release = threading.Event()
    old = FakeInitializer(entered=old_entered, release=old_release)
    new = FakeInitializer()
    old_controller = ReadinessController(
        config(tmp_path / "old", generation_id="old-generation"),
        initializer_factory=lambda _root: old,
    )
    new_controller = ReadinessController(
        config(tmp_path / "new", generation_id="new-generation"),
        initializer_factory=lambda _root: new,
    )
    old_controller.begin({})
    assert old_entered.wait(1)

    old_closer = threading.Thread(target=old_controller.close)
    old_closer.start()
    assert old.closed.wait(1)
    new_controller.begin({})
    deadline = time.monotonic() + 1
    while new_controller.snapshot()["readiness"] == "initializing":
        assert time.monotonic() < deadline
    old_release.set()
    old_closer.join(1)

    assert old_controller.snapshot()["readiness"] == "initializing"
    assert old_controller.snapshot()["revision"] == 1
    assert new_controller.snapshot()["generationId"] == "new-generation"
    assert new_controller.snapshot()["readiness"] == "ready"
    assert new_controller.snapshot()["revision"] == 2
    assert old.close_calls == 1
    new_controller.close()


def test_summary_is_copied_through_exact_five_field_allowlist(tmp_path: Path) -> None:
    unsafe = {**SUMMARY, "apiKey": "secret", "path": str(tmp_path)}
    initializer = FakeInitializer(readiness_result(summary=unsafe))
    controller = ReadinessController(config(tmp_path), initializer_factory=lambda _root: initializer)
    controller.begin({})
    deadline = time.monotonic() + 1
    while controller.snapshot()["readiness"] == "initializing":
        assert time.monotonic() < deadline
    snapshot = controller.snapshot()
    assert snapshot["currentCharacterSummary"] == SUMMARY
    assert set(snapshot["currentCharacterSummary"]) == set(SUMMARY)
    assert "secret" not in repr(snapshot)
    controller.close()


def test_control_dispatcher_closes_readiness_owner_once(tmp_path: Path) -> None:
    initializer = FakeInitializer()
    dispatcher = negotiated_dispatcher(tmp_path, lambda _root: initializer)
    dispatcher.dispatch(request("initialize", "core.initialize", {}))
    wait_for_readiness(dispatcher, "ready")
    dispatcher.close()
    dispatcher.close()
    assert initializer.close_calls == 1
