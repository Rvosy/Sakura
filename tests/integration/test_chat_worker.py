from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest


def test_event_worker_can_move_to_qthread_without_overriding_qobject_event() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")

    qtcore = pytest.importorskip("PySide6.QtCore")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtcore, "QThread") or not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    QThread = qtcore.QThread
    QApplication = qtwidgets.QApplication
    QWidget = qtwidgets.QWidget

    from app.agent import AgentEvent
    from app.core.chat_worker import EventWorker

    app = QApplication.instance() or QApplication([])
    parent = QWidget()
    thread = QThread(parent)
    worker = EventWorker(object(), AgentEvent(type="reminder_due", payload={}))  # type: ignore[arg-type]

    worker.moveToThread(thread)

    assert worker.thread() is thread
    # 确保 QThread 事件循环正常退出再删除，避免测试结束时 hang
    thread.quit()
    thread.wait(1000)
    thread.deleteLater()
    parent.deleteLater()
    app.processEvents()


def test_chat_worker_forwards_progress_signal() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    QApplication = qtwidgets.QApplication

    from app.agent import AgentProgress, AgentResult
    from app.llm.chat_reply import parse_chat_reply
    from app.core.chat_worker import ChatWorker

    class Runtime:
        def handle_user_message(self, _messages, progress_callback=None, cancel_checker=None):  # type: ignore[no-untyped-def]
            if cancel_checker is not None:
                cancel_checker()
            if progress_callback is not None:
                progress_callback(
                    AgentProgress(
                        reply=parse_chat_reply(
                            '{"segments":[{"ja":"調べるね。","zh":"我查一下。","tone":"中性"}]}'
                        )
                    )
                )
            return AgentResult(
                reply=parse_chat_reply(
                    '{"segments":[{"ja":"終わったよ。","zh":"完成了。","tone":"中性"}]}'
                )
            )

    app = QApplication.instance() or QApplication([])
    worker = ChatWorker(Runtime(), [{"role": "user", "content": "查一下"}])  # type: ignore[arg-type]
    progress_replies = []
    finished_replies = []
    worker.progress.connect(lambda progress: progress_replies.append(progress.reply.translation))
    worker.finished.connect(lambda result: finished_replies.append(result.reply.translation))

    worker.run()
    app.processEvents()

    assert progress_replies == ["我查一下。"]
    assert finished_replies == ["完成了。"]


def test_chat_worker_records_visual_observation_after_reply() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    QApplication = qtwidgets.QApplication

    from app.agent import AgentResult
    from app.llm.chat_reply import parse_chat_reply
    from app.core.chat_worker import ChatWorker
    from app.agent.screen_observation import ScreenObservation
    from app.storage.visual_observation import VisualObservationJob, VisualObservationStore

    class Runtime:
        def handle_user_message(self, _messages, progress_callback=None, cancel_checker=None):  # type: ignore[no-untyped-def]
            if cancel_checker is not None:
                cancel_checker()
            return AgentResult(
                reply=parse_chat_reply(
                    '{"segments":[{"ja":"覚えたよ。","zh":"我记下来了。","tone":"中性"}]}'
                ),
                visual_observation={
                    "summary": "截图里是一段聊天。",
                    "visible_texts": ["可以追问的台词"],
                    "uncertain_texts": [],
                    "notable_elements": ["聊天气泡"],
                    "confidence": 0.9,
                    "sensitive_redacted": False,
                },
            )

    app = QApplication.instance() or QApplication([])
    path = Path("data") / f"test_worker_visual_{uuid.uuid4().hex}.jsonl"
    try:
        worker = ChatWorker(
            Runtime(),  # type: ignore[arg-type]
            [{"role": "user", "content": "帮我看截图"}],
            visual_observation_store=VisualObservationStore(path),
            visual_observation_jobs=[
                VisualObservationJob(
                    id="vis_worker",
                    source="manual_screenshot",
                    user_text="帮我看截图",
                    observation=ScreenObservation(
                        data_url="data:image/jpeg;base64,worker",
                        width=320,
                        height=180,
                        captured_at="2026-05-31T12:00:00+08:00",
                        screen_name="manual-selection",
                    ),
                )
            ],
        )
        finished_replies = []
        worker.finished.connect(lambda result: finished_replies.append(result.reply.translation))

        worker.run()
        app.processEvents()

        raw = path.read_text(encoding="utf-8")
        assert "vis_worker" in raw
        assert "可以追问的台词" in raw
        assert "data:image" not in raw
        assert finished_replies == ["我记下来了。"]
    finally:
        path.unlink(missing_ok=True)


def test_event_worker_forwards_progress_signal() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    QApplication = qtwidgets.QApplication

    from app.agent import AgentEvent, AgentProgress, AgentResult
    from app.llm.chat_reply import parse_chat_reply
    from app.core.chat_worker import EventWorker

    class Runtime:
        def handle_event(self, _event, progress_callback=None, cancel_checker=None):  # type: ignore[no-untyped-def]
            if cancel_checker is not None:
                cancel_checker()
            if progress_callback is not None:
                progress_callback(
                    AgentProgress(
                        reply=parse_chat_reply(
                            '{"segments":[{"ja":"確認する。","zh":"我确认一下。","tone":"中性"}]}'
                        )
                    )
                )
            return AgentResult(
                reply=parse_chat_reply(
                    '{"segments":[{"ja":"大丈夫。","zh":"没问题。","tone":"中性"}]}'
                )
            )

    app = QApplication.instance() or QApplication([])
    worker = EventWorker(Runtime(), AgentEvent(type="proactive_check", payload={}))  # type: ignore[arg-type]
    progress_replies = []
    finished_replies = []
    worker.progress.connect(lambda progress: progress_replies.append(progress.reply.translation))
    worker.finished.connect(lambda result: finished_replies.append(result.reply.translation))

    worker.run()
    app.processEvents()

    assert progress_replies == ["我确认一下。"]
    assert finished_replies == ["没问题。"]


def test_chat_worker_cancel_suppresses_result_and_error() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    QApplication = qtwidgets.QApplication

    from app.agent import AgentResult
    from app.llm.chat_reply import parse_chat_reply
    from app.core.chat_worker import ChatWorker

    class Runtime:
        def handle_user_message(self, _messages, progress_callback=None, cancel_checker=None):  # type: ignore[no-untyped-def]
            if cancel_checker is not None:
                cancel_checker()
            return AgentResult(
                reply=parse_chat_reply(
                    '{"segments":[{"ja":"遅い返事。","zh":"迟到回复。","tone":"中性"}]}'
                )
            )

    app = QApplication.instance() or QApplication([])
    worker = ChatWorker(Runtime(), [{"role": "user", "content": "取消"}])  # type: ignore[arg-type]
    finished = []
    failed = []
    cancelled = []
    worker.finished.connect(lambda result: finished.append(result.reply.translation))
    worker.failed.connect(lambda message: failed.append(message))
    worker.cancelled.connect(lambda: cancelled.append(True))

    worker.cancel()
    worker.run()
    app.processEvents()

    assert finished == []
    assert failed == []
    assert cancelled == [True]
