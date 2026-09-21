import threading
from types import SimpleNamespace

import pytest

from app.core_host.real_chat import RealChatBoundary


def request(name, payload):
    return {"id": name, "name": name, "payload": payload}


@pytest.mark.parametrize("finish_during_read", [False, True])
def test_character_switch_rejects_old_capture_even_after_switching_back(tmp_path, monkeypatch, finish_during_read):
    role = ["alpha"]
    boundary = RealChatBoundary("generation", "a" * 32, tmp_path,
                               session_provider=lambda: role[0], timeline_store=object())
    token = boundary.handle_screen_session(request("screen.session", {}))["payload"]["sessionId"]
    entered, release = threading.Event(), threading.Event()
    reads = []
    def consume(*args, **kwargs):
        reads.append(role[0])
        if finish_during_read:
            entered.set()
            assert release.wait(3)
        return SimpleNamespace(width=10, height=10)
    monkeypatch.setattr("app.core_host.screen_capture.consume_screen_resource", consume)
    handler = boundary.handle_screen_attach
    payload = {"sessionId": token, "resource": {}}
    failures = []
    def attach():
        try:
            handler(request("attach", payload))
        except Exception as error:
            failures.append(error)
    worker = threading.Thread(target=attach)
    if finish_during_read:
        worker.start()
        assert entered.wait(3)
    for target in ("beta", "alpha"):
        with boundary.suspend_for_character_change():
            role[0] = target
    if not finish_during_read:
        worker.start()
    release.set()
    worker.join(3)
    assert not worker.is_alive()
    assert len(failures) == 1 and str(failures[0]) == "SCREEN_SESSION_STALE"
    assert boundary._pending_screen_attachment is None
    assert reads == (["alpha"] if finish_during_read else [])
    payload["sessionId"] = boundary.handle_screen_session(request("screen.session", {}))["payload"]["sessionId"]
    assert payload["sessionId"] != token
    assert handler(request("attach-new", payload))["payload"]["attached"] is True
