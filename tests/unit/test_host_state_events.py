"""State notifications follow the same admission facts as plugin queries."""
from types import SimpleNamespace

from app.core_host.chat_host import ChatHost
from app.core_host.real_chat import ChatTurnInput, RealChatBoundary


def test_chat_state_notifications_cover_core_desktop_and_session_changes(tmp_path):
    session = SimpleNamespace(character=SimpleNamespace(id="character", display_name="角色"))
    boundary = RealChatBoundary("generation", "credential", tmp_path, session_provider=lambda: session)
    events = []
    host = ChatHost(boundary_provider=lambda: boundary, screen_host=None,
                    emit_callback=lambda *_: None, notify_callback=lambda name, value: events.append((name, value)))
    boundary.set_host_state_listener(host.notify_state)
    session_id = host.current()["sessionId"]

    def desktop(idle, revision, identity=session_id):
        return host.set_ui_state({"sessionId": identity, "idle": idle, "activityRevision": revision})

    try:
        assert events[-1][1]["idle"] is False
        desktop(True, 1)
        assert events[-1][1]["idle"] is True
        count = len(events)
        desktop(True, 2)
        assert len(events) == count, "revision-only updates do not wake observers"
        assert not desktop(False, 1)["accepted"]
        assert len(events) == count

        boundary._reserve_turn(ChatTurnInput("turn", "hello"))
        assert events[-1][1]["idle"] is False
        boundary.abandon_send({"id": "turn"})
        assert events[-1][1]["idle"] is True
        with boundary.idle_runtime_update():
            assert events[-1][1]["idle"] is False
        assert events[-1][1]["idle"] is True

        desktop(False, 3)
        boundary._reserve_turn(ChatTurnInput("turn-2", "hello"))
        count = len(events)
        boundary.abandon_send({"id": "turn-2"})
        assert len(events) == count, "Core completion cannot announce idle while playback is busy"
        desktop(True, 4)
        assert events[-1][1]["idle"] is True

        with boundary.suspend_for_character_change():
            assert events[-1][1]["sessionId"] is None
        fresh = host.current()["sessionId"]
        assert fresh != session_id
        assert events[-1][1]["idle"] is False
        assert not desktop(True, 5)["accepted"]
        desktop(True, 0, fresh)
        assert events[-1][1]["idle"] is True
        host.invalidate_session()
        assert events[-1][1]["idle"] is False
        boundary.close()
        assert events[-1][1]["sessionId"] is None
        assert {name for name, _ in events} == {"sakura.host.chat.state.changed"}
    finally:
        boundary.close()
        host.close()
