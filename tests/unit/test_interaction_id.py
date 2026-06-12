"""tests/unit/test_interaction_id.py — 交互 ID 贯通测试。

覆盖：
- ContextVar 的设置/读取/清空
- debug_log 自动附加 interaction_id（dict / None / 已显式给出三种形态）
- 跨线程传递语义（线程入口恢复后日志可串联）
"""

from __future__ import annotations

import threading

from app.core.debug_log import format_debug_data, sanitize_debug_data, _attach_interaction_id
from app.core.interaction import clear_interaction_id, get_interaction_id, set_interaction_id


class TestContextVar:
    def teardown_method(self) -> None:
        clear_interaction_id()

    def test_set_get_clear(self) -> None:
        assert get_interaction_id() == ""
        set_interaction_id("interaction-42")
        assert get_interaction_id() == "interaction-42"
        clear_interaction_id()
        assert get_interaction_id() == ""

    def test_thread_does_not_inherit_then_restores(self) -> None:
        set_interaction_id("interaction-7")
        seen: dict[str, str] = {}

        def worker() -> None:
            # 新线程不自动继承 ContextVar
            seen["before"] = get_interaction_id()
            # 模拟 worker 入口恢复（ChatWorker.run / _request_audio 的做法）
            set_interaction_id("interaction-7")
            seen["after"] = get_interaction_id()

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
        assert seen["before"] == ""
        assert seen["after"] == "interaction-7"
        # 主线程上下文不受影响
        assert get_interaction_id() == "interaction-7"


class TestDebugLogAttachment:
    def teardown_method(self) -> None:
        clear_interaction_id()

    def test_attaches_to_dict(self) -> None:
        set_interaction_id("interaction-1")
        data = _attach_interaction_id({"key": "value"})
        assert data["interaction_id"] == "interaction-1"
        assert data["key"] == "value"

    def test_attaches_to_none(self) -> None:
        set_interaction_id("interaction-2")
        assert _attach_interaction_id(None) == {"interaction_id": "interaction-2"}

    def test_no_id_no_change(self) -> None:
        assert _attach_interaction_id({"key": "v"}) == {"key": "v"}
        assert _attach_interaction_id(None) is None

    def test_explicit_id_not_overwritten(self) -> None:
        set_interaction_id("interaction-3")
        data = _attach_interaction_id({"interaction_id": "explicit"})
        assert data["interaction_id"] == "explicit"

    def test_non_dict_data_untouched(self) -> None:
        set_interaction_id("interaction-4")
        assert _attach_interaction_id("raw string") == "raw string"

    def test_id_survives_sanitization(self) -> None:
        set_interaction_id("interaction-5")
        data = sanitize_debug_data(_attach_interaction_id({"api_key": "secret"}))
        assert data["interaction_id"] == "interaction-5"
        assert data["api_key"] != "secret"  # 脱敏照常生效

    def test_format_debug_data_includes_id(self) -> None:
        set_interaction_id("interaction-6")
        assert "interaction-6" in format_debug_data(_attach_interaction_id(None))
