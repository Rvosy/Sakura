from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
import types

import pytest

_STUBBED_PYSIDE = False
if importlib.util.find_spec("PySide6") is None:
    _STUBBED_PYSIDE = True
    pyside_module = types.ModuleType("PySide6")
    qtcore_module = types.ModuleType("PySide6.QtCore")
    qtwidgets_module = types.ModuleType("PySide6.QtWidgets")
    pyside_module.__spec__ = importlib.machinery.ModuleSpec("PySide6", loader=None)
    qtcore_module.__spec__ = importlib.machinery.ModuleSpec("PySide6.QtCore", loader=None)
    qtwidgets_module.__spec__ = importlib.machinery.ModuleSpec("PySide6.QtWidgets", loader=None)

    class _Flag:
        def __or__(self, _other: object) -> "_Flag":
            return self

    class Qt:
        class AlignmentFlag:
            AlignCenter = _Flag()
            AlignLeft = _Flag()
            AlignRight = _Flag()

        class TextFormat:
            PlainText = object()

        class TextInteractionFlag:
            LinksAccessibleByMouse = _Flag()
            TextSelectableByMouse = _Flag()

    class QTimer:
        @staticmethod
        def singleShot(*_args: object, **_kwargs: object) -> None:
            pass

    class _WidgetStub:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

    class QFrame(_WidgetStub):
        class Shape:
            NoFrame = object()

    class QMessageBox:
        class StandardButton:
            Yes = object()
            No = object()

    qtcore_module.QTimer = QTimer
    qtcore_module.Qt = Qt
    qtwidgets_module.QDialog = _WidgetStub
    qtwidgets_module.QFrame = QFrame
    qtwidgets_module.QHBoxLayout = _WidgetStub
    qtwidgets_module.QLabel = _WidgetStub
    qtwidgets_module.QMessageBox = QMessageBox
    qtwidgets_module.QPushButton = _WidgetStub
    qtwidgets_module.QScrollArea = _WidgetStub
    qtwidgets_module.QVBoxLayout = _WidgetStub
    qtwidgets_module.QWidget = _WidgetStub
    sys.modules["PySide6"] = pyside_module
    sys.modules["PySide6.QtCore"] = qtcore_module
    sys.modules["PySide6.QtWidgets"] = qtwidgets_module

from app.agent.proactive_care import PROACTIVE_SCREEN_CONTEXT_HISTORY_MARKER
from app.agent.screen_observation import (
    MANUAL_SCREEN_OBSERVATION_HISTORY_MARKER,
    SCREEN_OBSERVATION_HISTORY_MARKER,
)
from app.storage.chat_history import ChatHistoryEntry
from app.ui.history_window import _entry_view_model

if _STUBBED_PYSIDE:
    sys.modules.pop("PySide6.QtWidgets", None)
    sys.modules.pop("PySide6.QtCore", None)
    sys.modules.pop("PySide6", None)


def _entry(role: str, content: str, translation: str = "") -> ChatHistoryEntry:
    return ChatHistoryEntry(
        created_at="2026-05-30T16:20:30+08:00",
        role=role,
        content=content,
        translation=translation,
    )


def _finish_batched_history_render(window) -> None:  # type: ignore[no-untyped-def]
    while getattr(window, "_staged_history_content", None) is not None:
        window._render_next_batch(window._render_generation)


def test_entry_view_model_uses_distinct_role_layouts() -> None:
    user_view = _entry_view_model(_entry("user", "你好"), "ja", "桜")
    assistant_view = _entry_view_model(_entry("assistant", "こんばんは"), "ja", "桜")
    error_view = _entry_view_model(_entry("error", "请求失败"), "ja", "桜")
    system_view = _entry_view_model(_entry("system", "已附加当前屏幕截图"), "ja", "桜")

    assert user_view.meta_text == "你 · 2026-05-30 16:20:30"
    assert user_view.align == "right"
    assert user_view.bubble_object_name == "userBubble"
    assert assistant_view.meta_text == "桜 · 2026-05-30 16:20:30"
    assert assistant_view.align == "left"
    assert assistant_view.bubble_object_name == "assistantBubble"
    assert error_view.role_name == "错误"
    assert error_view.bubble_object_name == "errorBubble"
    assert system_view.role_name == "系统记录"
    assert system_view.align == "center"
    assert system_view.bubble_object_name == "systemBubble"


def test_entry_view_model_keeps_plain_text_content() -> None:
    view = _entry_view_model(_entry("user", "<script>x</script> & one\ntwo"), "ja", "桜")

    assert view.content == "<script>x</script> & one\ntwo"


def test_entry_view_model_humanizes_screen_observation_markers() -> None:
    manual_view = _entry_view_model(
        _entry(
            "user",
            f"你了解这个游戏吗\n{MANUAL_SCREEN_OBSERVATION_HISTORY_MARKER[:-1]}，视觉记录 visual_id=vis_test]",
        ),
        "ja",
        "桜",
    )
    autonomous_view = _entry_view_model(
        _entry(
            "system",
            f"{SCREEN_OBSERVATION_HISTORY_MARKER[:-1]}，视觉记录 visual_id=vis_auto]",
        ),
        "ja",
        "桜",
    )
    proactive_view = _entry_view_model(
        _entry("system", PROACTIVE_SCREEN_CONTEXT_HISTORY_MARKER),
        "ja",
        "桜",
    )

    assert manual_view.content == "你了解这个游戏吗\n（已附上你框选的画面）"
    assert autonomous_view.content == "（已看过当前屏幕）"
    assert proactive_view.content == "刚才留意了一下屏幕状态。"
    assert "visual_id" not in manual_view.content
    assert "visual_id" not in autonomous_view.content


def test_entry_view_model_uses_translation_only_for_chinese_assistant_subtitles() -> None:
    entry = _entry("assistant", "原文", "译文")

    zh_view = _entry_view_model(entry, "zh", "桜")
    ja_view = _entry_view_model(entry, "ja", "桜")

    assert zh_view.content == "译文"
    assert ja_view.content == "原文"


def test_entry_view_model_recovers_json_string_assistant_history() -> None:
    entry = _entry(
        "assistant",
        '{"segments":[{"ja":"一つ目。","zh":"第一段。","tone":"中性"},'
        '{"ja":"二つ目。","zh":"第二段。","tone":"中性"}]}',
    )

    zh_view = _entry_view_model(entry, "zh", "桜")
    ja_view = _entry_view_model(entry, "ja", "桜")

    assert zh_view.content == "第一段。\n第二段。"
    assert ja_view.content == "一つ目。\n二つ目。"


def test_entry_view_model_ignores_tone_and_portrait_metadata() -> None:
    entry = ChatHistoryEntry(
        created_at="2026-05-30T16:20:30+08:00",
        role="assistant",
        content="原文",
        translation="译文",
        tone="困惑",
        portrait="张嘴疑问",
    )

    view = _entry_view_model(entry, "zh", "桜")

    assert view.content == "译文"
    assert view.meta_text == "桜 · 2026-05-30 16:20:30"


def test_history_window_keeps_meta_outside_message_bubble(qtbot) -> None:  # type: ignore[no-untyped-def]
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not all(hasattr(qtwidgets, name) for name in ("QApplication", "QFrame", "QLabel")):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from app.ui.history_window import HistoryWindow

    QApplication = qtwidgets.QApplication
    QFrame = qtwidgets.QFrame
    QLabel = qtwidgets.QLabel
    app = QApplication.instance() or QApplication([])

    class StaticHistoryStore:
        assistant_name = "桜"

        def load(self) -> list[ChatHistoryEntry]:
            return [
                _entry("user", "你好"),
                _entry("assistant", "こんばんは"),
                _entry("system", "系统记录"),
            ]

    store = StaticHistoryStore()

    window = HistoryWindow(store)  # type: ignore[arg-type]
    qtbot.addWidget(window)
    window.refresh()

    meta_labels = window.findChildren(QLabel, "entryMeta")
    bubbles = [
        *window.findChildren(QFrame, "userBubble"),
        *window.findChildren(QFrame, "assistantBubble"),
        *window.findChildren(QFrame, "systemBubble"),
    ]

    assert len(meta_labels) == 3
    assert len(bubbles) == 3
    for bubble in bubbles:
        assert not any(meta.parent() is bubble for meta in meta_labels)
        assert bubble.findChild(QLabel, "entryText") is not None or bubble.findChild(QLabel, "systemText") is not None
    window.close()


def test_history_window_groups_consecutive_role_meta(qtbot) -> None:  # type: ignore[no-untyped-def]
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not all(hasattr(qtwidgets, name) for name in ("QApplication", "QFrame", "QLabel")):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from app.ui.history_window import HistoryWindow

    QApplication = qtwidgets.QApplication
    QFrame = qtwidgets.QFrame
    QLabel = qtwidgets.QLabel
    app = QApplication.instance() or QApplication([])

    class StaticHistoryStore:
        assistant_name = "桜"

        def load(self) -> list[ChatHistoryEntry]:
            return [
                _entry("user", "请总结一下"),
                _entry("assistant", "第一段"),
                _entry("assistant", "第二段"),
                _entry("assistant", "第三段"),
                _entry("user", "继续"),
            ]

    window = HistoryWindow(StaticHistoryStore())  # type: ignore[arg-type]
    qtbot.addWidget(window)
    window.refresh()

    meta_texts = [label.text() for label in window.findChildren(QLabel, "entryMeta")]

    assert len(window.findChildren(QFrame, "assistantBubble")) == 3
    assert meta_texts.count("桜 · 2026-05-30 16:20:30") == 1
    assert meta_texts.count("你 · 2026-05-30 16:20:30") == 2
    window.close()


def test_history_window_hides_stale_content_before_reopen_refresh(qtbot) -> None:  # type: ignore[no-untyped-def]
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not all(hasattr(qtwidgets, name) for name in ("QApplication", "QLabel")):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from app.ui.history_window import HistoryWindow

    QApplication = qtwidgets.QApplication
    QLabel = qtwidgets.QLabel
    app = QApplication.instance() or QApplication([])

    class MutableHistoryStore:
        assistant_name = "桜"

        def __init__(self) -> None:
            self.entries = [_entry("user", "旧内容")]

        def load(self) -> list[ChatHistoryEntry]:
            return list(self.entries)

    def label_texts() -> list[str]:
        return [label.text() for label in window.findChildren(QLabel)]

    store = MutableHistoryStore()
    window = HistoryWindow(store)  # type: ignore[arg-type]
    qtbot.addWidget(window)
    window.refresh()
    assert "旧内容" in label_texts()

    store.entries = [_entry("user", "新内容")]
    window._show_loading_state()
    window.request_refresh()

    texts_before_refresh = label_texts()
    assert "正在读取历史记录..." in texts_before_refresh
    assert "旧内容" not in texts_before_refresh

    window._run_scheduled_refresh()
    texts_after_refresh = label_texts()
    assert "新内容" in texts_after_refresh
    assert "旧内容" not in texts_after_refresh
    window.close()


def test_history_window_keeps_loading_visible_until_batched_render_finishes(qtbot) -> None:  # type: ignore[no-untyped-def]
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not all(hasattr(qtwidgets, name) for name in ("QApplication", "QLabel")):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from app.ui.history_window import HistoryWindow

    QApplication = qtwidgets.QApplication
    QLabel = qtwidgets.QLabel
    app = QApplication.instance() or QApplication([])

    class LargeHistoryStore:
        assistant_name = "桜"

        def load(self) -> list[ChatHistoryEntry]:
            return [_entry("user", f"历史内容 {index}") for index in range(41)]

    window = HistoryWindow(LargeHistoryStore())  # type: ignore[arg-type]
    qtbot.addWidget(window)
    window.refresh()

    texts_before_second_batch = [label.text() for label in window.findChildren(QLabel)]
    assert "正在读取历史记录..." in texts_before_second_batch
    assert "历史内容 0" not in texts_before_second_batch

    window._render_next_batch(window._render_generation)
    texts_after_render = [label.text() for label in window.findChildren(QLabel)]
    assert "历史内容 0" in texts_after_render
    assert "历史内容 40" in texts_after_render
    assert "正在读取历史记录..." not in texts_after_render
    window.close()


def test_history_window_scrolls_to_bottom_after_batched_layout_settles(qtbot) -> None:  # type: ignore[no-untyped-def]
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not all(hasattr(qtwidgets, name) for name in ("QApplication", "QLabel")):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from app.ui.history_window import HistoryWindow

    QApplication = qtwidgets.QApplication
    QLabel = qtwidgets.QLabel
    app = QApplication.instance() or QApplication([])

    class TallHistoryStore:
        assistant_name = "桜"

        def load(self) -> list[ChatHistoryEntry]:
            entries: list[ChatHistoryEntry] = []
            for index in range(90):
                entries.append(
                    _entry(
                        "assistant" if index % 2 else "user",
                        f"历史内容 {index} " + "这是一段用于撑高历史气泡的长文本。" * 3,
                    )
                )
            entries.append(_entry("assistant", "末尾内容"))
            return entries

    window = HistoryWindow(TallHistoryStore())  # type: ignore[arg-type]
    qtbot.addWidget(window)
    window.resize(620, 680)
    window.refresh()

    _finish_batched_history_render(window)
    assert "末尾内容" in [label.text() for label in window.findChildren(QLabel)]
    window._sync_history_layout()
    scrollbar = window.history_view.verticalScrollBar()
    assert scrollbar.maximum() > 0
    assert scrollbar.value() == scrollbar.maximum()
    window.close()
