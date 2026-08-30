from __future__ import annotations

import base64
import json
import shutil
import socket
import threading
import time
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agent.tools import ToolRegistry
from app.config.character_loader import CharacterRegistry
from app.core_host.plugin_runtime_application import PluginRuntimeApplication
from app.core_host.real_chat import RealChatBoundary, RealChatRejection
from app.llm.chat_reply import ChatReply, ChatSegment
from app.plugins.inventory import PluginInventory
from app.storage.runtime_roots import RuntimeRoots
from app.storage.timeline import NewTimelineEntry, TimelineKind, TimelineStore
from app.storage.paths import StoragePaths


def _roots(tmp_path: Path, port: int) -> RuntimeRoots:
    repository = Path(__file__).parents[2]
    distribution = tmp_path / "distribution"
    bundled = distribution / "plugins" / "builtin"
    bundled.mkdir(parents=True)
    shutil.copytree(
        repository / "plugins" / "builtin" / "sakura_mobile",
        bundled / "sakura_mobile",
    )
    user = tmp_path / "user"
    character = user / "characters" / "sakura"
    character.mkdir(parents=True)
    (character / "card.md").write_text("system prompt", encoding="utf-8")
    (character / "portrait.png").write_bytes(b"portrait")
    (character / "character.json").write_text(
        json.dumps({
            "id": "sakura",
            "display_name": "Sakura",
            "initial_message": "你好",
            "card": "card.md",
            "portrait": {"default": "portrait.png"},
        }),
        encoding="utf-8",
    )
    config = user / "data" / "plugins" / "sakura_mobile" / "config.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps({
            "enabled": True,
            "host": "127.0.0.1",
            "port": port,
            "token": "mobile-token",
        }),
        encoding="utf-8",
    )
    timeline = TimelineStore(StoragePaths(user).timeline_database())
    timeline.initialize()
    timeline.append_many([
        NewTimelineEntry(
            "entry-user",
            "turn-1",
            "sakura",
            TimelineKind.HUMAN,
            "chat",
            "2026-08-28T01:00:00+08:00",
            {"text": "历史问题"},
        ),
        NewTimelineEntry(
            "entry-assistant",
            "turn-1",
            "sakura",
            TimelineKind.ASSISTANT,
            "chat",
            "2026-08-28T01:00:01+08:00",
            {"segments": [{
                "text": "history reply",
                "translation": "历史回答",
                "tone": "中性",
                "portrait": "default",
                "suppressTts": False,
            }]},
        ),
    ])
    return RuntimeRoots(distribution, user)


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _json(
    url: str,
    *,
    payload: dict[str, object] | None = None,
    timeout: float = 2,
) -> dict[str, object]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        headers={"Content-Type": "application/json"} if payload is not None else {},
        method="POST" if payload is not None else "GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        value = json.loads(response.read())
    assert isinstance(value, dict)
    return value


def test_mobile_v4_runs_real_http_server_through_core_host_service(tmp_path: Path) -> None:
    port = _free_port()
    roots = _roots(tmp_path, port)
    profile = CharacterRegistry(roots.user_root).get("sakura")
    calls: list[tuple[str, str]] = []

    class ChatBoundary:
        def run_host_message(
            self,
            text: str,
            image: str,
            *,
            operation_id: str,
        ) -> dict[str, object]:
            assert operation_id.startswith("mobile-")
            calls.append((text, image))
            return {
                "reply": "手机回答",
                "reply_raw": "mobile reply",
                "segments": [{
                    "content": "手机回答",
                    "raw_content": "mobile reply",
                    "translation": "手机回答",
                    "tone": "中性",
                    "portrait": "default",
                }],
                "actions": [],
            }

    runtime = SimpleNamespace(set_context_providers=lambda _values: None)
    session = SimpleNamespace(character=profile, runtime=runtime)
    application = PluginRuntimeApplication(
        roots,
        "generation-mobile-v4",
        ToolRegistry(),
        PluginInventory(roots).scan().runtime_specs,
        call_timeout=1.0,
    )
    application.bind_chat_boundary(ChatBoundary())
    application.bind_runtime(ToolRegistry(), runtime, session=session)
    try:
        application.start()
        record = application.public_snapshot()["plugins"][0]
        assert record["pluginId"] == "sakura_mobile"
        assert record["state"] == "active"
        assert record["pid"] not in {None, 0}

        base = f"http://127.0.0.1:{port}"
        assert _json(f"{base}/api/status?token=mobile-token") == {"ok": True}
        characters = _json(f"{base}/api/characters?token=mobile-token")
        assert characters == {"characters": [{
            "id": "sakura",
            "name": "Sakura",
            "initial_message": "你好",
            "current": "true",
        }]}
        history = _json(
            f"{base}/api/history?token=mobile-token&character_id=sakura&limit=50"
        )
        assert [item["content"] for item in history["history"]] == [
            "历史问题",
            "历史回答",
        ]
        result = _json(f"{base}/api/chat", payload={
            "token": "mobile-token",
            "character_id": "sakura",
            "text": "你好，手机端",
            "image": "",
        })
        assert result["reply"] == "手机回答"
        assert calls == [("你好，手机端", "")]
    finally:
        application.close()

    with socket.socket() as probe:
        probe.settimeout(0.2)
        assert probe.connect_ex(("127.0.0.1", port)) != 0


def test_mobile_v4_slow_real_chat_and_large_image_stay_off_shell_transport(
    tmp_path: Path,
) -> None:
    port = _free_port()
    roots = _roots(tmp_path, port)
    profile = CharacterRegistry(roots.user_root).get("sakura")
    pipeline_messages: list[object] = []
    shell_events: list[dict[str, object]] = []

    class Pipeline:
        def run_user_message(self, messages, **_kwargs):  # type: ignore[no-untyped-def]
            pipeline_messages.extend(messages)
            time.sleep(3.2)
            return SimpleNamespace(
                reply=ChatReply([ChatSegment("mobile raw", translation="手机回答")]),
                actions=[],
            )

    runtime = SimpleNamespace(
        set_context_providers=lambda _values: None,
        finish_trace_operation=lambda *_args, **_kwargs: True,
    )
    session = SimpleNamespace(
        character=profile,
        runtime=runtime,
        pipeline=Pipeline(),
        tool_actions=None,
        memory_boundary=None,
    )
    timeline = TimelineStore(StoragePaths(roots.user_root).timeline_database())
    boundary = RealChatBoundary(
        "generation-mobile-real",
        "44" * 16,
        roots.user_root,
        session_provider=lambda: session,
        timeline_store=timeline,
        event_publisher=lambda value: (
            shell_events.append(value),
            (_ for _ in ()).throw(RuntimeError("UNKNOWN_REQUEST_ID")),
        )[1],
    )
    application = PluginRuntimeApplication(
        roots,
        "generation-mobile-real",
        ToolRegistry(),
        PluginInventory(roots).scan().runtime_specs,
        call_timeout=1.0,
    )
    application.bind_chat_boundary(boundary)
    application.bind_runtime(ToolRegistry(), runtime, session=session)
    image = "data:image/jpeg;base64," + base64.b64encode(b"x" * 1_100_000).decode("ascii")
    try:
        application.start()
        result = _json(
            f"http://127.0.0.1:{port}/api/chat",
            payload={
                "token": "mobile-token",
                "character_id": "sakura",
                "text": "看看图片",
                "image": image,
            },
            timeout=10,
        )
        assert result["reply"] == "手机回答"
        assert shell_events == []
        assert application._host_services.artifact_count == 0
        assert any(
            isinstance(message, dict)
            and isinstance(message.get("content"), list)
            and any(
                isinstance(part, dict)
                and part.get("type") == "image_url"
                and str(part.get("image_url", {}).get("url", "")).startswith(
                    "data:image/jpeg;base64,"
                )
                for part in message["content"]
            )
            for message in pipeline_messages
        )
    finally:
        application.close()
        boundary.close()


def test_mobile_host_image_busy_does_not_poison_the_next_attachment(tmp_path: Path) -> None:
    pipeline_started = threading.Event()
    release_pipeline = threading.Event()
    calls = 0

    class Pipeline:
        def run_user_message(self, _messages, **_kwargs):  # type: ignore[no-untyped-def]
            nonlocal calls
            calls += 1
            if calls == 1:
                pipeline_started.set()
                assert release_pipeline.wait(3)
            return SimpleNamespace(reply=ChatReply([ChatSegment("ok")]), actions=[])

    session = SimpleNamespace(
        character=SimpleNamespace(id="sakura", display_name="Sakura"),
        runtime=SimpleNamespace(finish_trace_operation=lambda *_args, **_kwargs: True),
        pipeline=Pipeline(),
        tool_actions=None,
        memory_boundary=None,
    )
    store = TimelineStore(tmp_path / "timeline.sqlite3")
    store.initialize()
    boundary = RealChatBoundary(
        "generation-mobile-busy",
        "45" * 16,
        tmp_path,
        session_provider=lambda: session,
        timeline_store=store,
    )
    desktop_request = {
        "id": "desktop-chat",
        "kind": "request",
        "name": "chat.send",
        "generationId": "generation-mobile-busy",
        "generationCredential": "45" * 16,
        "payload": {"message": "desktop", "operationId": "desktop-chat"},
    }
    boundary.reserve_send(desktop_request)
    desktop = threading.Thread(target=boundary.handle_send, args=(desktop_request,))
    desktop.start()
    assert pipeline_started.wait(2)
    image = "data:image/png;base64," + base64.b64encode(b"image").decode("ascii")
    with pytest.raises(RealChatRejection) as rejected:
        boundary.run_host_message(
            "busy",
            image,
            operation_id="mobile-" + "1" * 32,
        )
    assert rejected.value.code == "CHAT_EXECUTION_LIMIT_EXCEEDED"

    release_pipeline.set()
    desktop.join(3)
    assert not desktop.is_alive()
    result = boundary.run_host_message(
        "after busy",
        image,
        operation_id="mobile-" + "2" * 32,
    )
    assert result["reply_raw"] == "ok"
    boundary.close()
