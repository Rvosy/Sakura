from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from app.core_host.chat_host import ChatHost
from app.core_host.history import HistoryBoundary
from app.core_host.real_chat import RealChatBoundary, RealChatRejection
from app.plugins.host_services import HOST_CALLER, HOST_CALLER_SCOPE
from app.storage.paths import StoragePaths
from app.storage.timeline import TimelineStore


@contextmanager
def caller():
    owner = HOST_CALLER.set("sakura.gomoku")
    scope = HOST_CALLER_SCOPE.set("plugin-scope")
    try:
        yield
    finally:
        HOST_CALLER_SCOPE.reset(scope)
        HOST_CALLER.reset(owner)


@pytest.mark.parametrize("extended", [False, True])
def test_activity_is_saved_while_chat_busy_and_reopens_through_reloaded_plugin(tmp_path, extended):
    timeline = TimelineStore(StoragePaths(tmp_path).timeline_database())
    timeline.initialize()
    session = SimpleNamespace(character=SimpleNamespace(id="pet", display_name="伙伴"))
    boundary = RealChatBoundary("generation", "credential", tmp_path,
        session_provider=lambda: session, timeline_store=timeline)
    events, opened = [], []
    host = ChatHost(boundary_provider=lambda: boundary, screen_host=None,
        emit_callback=lambda *event: events.append(event),
        invoke_callback=lambda *args: opened.append(args))
    assert not host.current()["idle"]
    request = {"sessionId": host.current()["sessionId"], "text": "结束了五子棋",
        "details": "你获胜，共 9 手。", "recordId": "game-1", "actionLabel": "查看棋谱"}
    if extended:
        request.update(recordId="game/" + "round/" * 30 + "1", details="1. A1 B2\n" * 9000,
                       actionLabel="查看这场比赛的完整棋谱与逐回合说明" * 8)
    with caller():
        host.register_record_opener("old-handle")
        saved = host.record_activity(request)
    history = HistoryBoundary("generation", "credential", tmp_path,
        session_provider=lambda: session, application_provider=lambda: SimpleNamespace(chat=host))
    envelope = {"generationId": "generation", "generationCredential": "credential", "id": "history",
        "name": "ui.history.page", "payload": {"expectedCharacterId": "pet", "beforeCursor": None, "limit": 50}}
    page = history.handle(envelope)
    assert page["ok"]
    entries = page["payload"]["entries"]
    assert len(entries) == 1
    assert entries[0]["payload"] == {"text": request["text"], "activity": {
        "sourcePluginId": "sakura.gomoku", "recordId": request["recordId"],
        "details": request["details"], "actionLabel": request["actionLabel"]}}
    assert events[0][0] == "host.history.changed"
    assert events[0][1]["entryId"] == saved["entryId"]

    envelope.update(name="ui.history.open_record", payload={"expectedCharacterId": "pet", "entryId": saved["entryId"]})
    host.revoke_scope("sakura.gomoku")
    assert history.handle(envelope)["error"]["code"] == "HISTORY_PLUGIN_UNAVAILABLE"
    with caller():
        host.register_record_opener("new-handle")
    assert history.handle(envelope)["payload"] == {"opened": True}
    assert opened == [("new-handle", "chat.history.open", request["recordId"])]

    session.character.id = "another-pet"
    assert history.handle(envelope)["error"]["code"] == "HISTORY_CHARACTER_MISMATCH"
    envelope["payload"]["expectedCharacterId"] = "another-pet"
    assert history.handle(envelope)["error"]["code"] == "HISTORY_RECORD_NOT_FOUND"
    assert len(opened) == 1
    with caller(), pytest.raises(RealChatRejection, match="CHAT_SESSION_STALE"):
        host.record_activity({**request, "sessionId": "old-session"})
    assert len(timeline.read_all("pet")) == 1
