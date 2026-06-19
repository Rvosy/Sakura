"""tests/unit/test_tts_service_state.py — TTS 服务生命周期状态机测试。

覆盖：
- 状态转移记录与测试桩容错
- 端点解析（合法/非法地址）
- 统一就绪轮询：进程退出失败、就绪成功、超时失败
- 失败状态不缓存（下次请求重新探测）
"""

from __future__ import annotations

import types
from pathlib import Path

from app.voice.tts import (
    TTSServiceState,
    _parse_service_endpoint,
    _set_service_state,
    _wait_local_service_ready,
)


def _stub_provider(*, provider: str = "gpt-sovits", api_url: str = "http://127.0.0.1:9880/tts"):
    stub = types.SimpleNamespace()
    stub.settings = types.SimpleNamespace(provider=provider, api_url=api_url, timeout_seconds=3)
    stub._server_process = None
    stub._base_dir = Path("D:/fake") if Path("D:/").exists() else Path("/fake")
    return stub


class TestParseEndpoint:
    def test_explicit_port(self) -> None:
        assert _parse_service_endpoint("http://127.0.0.1:9880/tts") == ("127.0.0.1", 9880)

    def test_default_ports(self) -> None:
        assert _parse_service_endpoint("http://example.test/tts") == ("example.test", 80)
        assert _parse_service_endpoint("https://example.test/tts") == ("example.test", 443)

    def test_invalid_port(self) -> None:
        assert _parse_service_endpoint("http://127.0.0.1:notaport/tts") is None

    def test_missing_host(self) -> None:
        assert _parse_service_endpoint("not-a-url") is None


class TestStateTransitions:
    def test_records_transition(self) -> None:
        stub = _stub_provider()
        _set_service_state(stub, TTSServiceState.PROBING)
        assert stub._service_state == TTSServiceState.PROBING
        _set_service_state(stub, TTSServiceState.READY, {"via": "probe"})
        assert stub._service_state == TTSServiceState.READY

    def test_tolerates_stub_without_attribute(self) -> None:
        stub = _stub_provider()
        # SimpleNamespace 没有预置 _service_state，首次设置走 getattr 默认值
        _set_service_state(stub, TTSServiceState.STARTING)
        assert stub._service_state == TTSServiceState.STARTING


class _ExitedProcess:
    def poll(self) -> int:
        return 7


class _AliveProcess:
    def poll(self) -> None:
        return None


class TestWaitLocalServiceReady:
    def test_process_exit_fails_with_log_path(self) -> None:
        stub = _stub_provider()
        stub._server_process = _ExitedProcess()
        messages: list[str] = []
        ok = _wait_local_service_ready(
            provider=stub,
            service_name="GPT-SoVITS",
            ready_check=lambda: False,
            fail_callback=messages.append,
            timeout_seconds=3,
        )
        assert not ok
        assert stub._service_state == TTSServiceState.FAILED
        assert "退出码：7" in messages[0]
        assert "启动日志" in messages[0]

    def test_ready_check_success(self) -> None:
        stub = _stub_provider()
        stub._server_process = _AliveProcess()
        checks: list[int] = []

        def ready() -> bool:
            checks.append(1)
            return len(checks) >= 2  # 第二次轮询就绪

        ok = _wait_local_service_ready(
            provider=stub,
            service_name="GPT-SoVITS",
            ready_check=ready,
            fail_callback=lambda _m: None,
            timeout_seconds=5,
        )
        assert ok
        assert len(checks) == 2

    def test_timeout_fails(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        stub = _stub_provider(provider="genie-tts", api_url="http://127.0.0.1:9881/")
        stub._server_process = _AliveProcess()
        # 压缩等待：sleep 跳过、时间快进
        clock = {"now": 0.0}
        monkeypatch.setattr("app.voice.tts_service.time.monotonic", lambda: clock["now"])

        def fast_sleep(seconds: float) -> None:
            clock["now"] += seconds

        monkeypatch.setattr("app.voice.tts_service.time.sleep", fast_sleep)
        messages: list[str] = []
        ok = _wait_local_service_ready(
            provider=stub,
            service_name="Genie TTS",
            ready_check=lambda: False,
            fail_callback=messages.append,
            timeout_seconds=3,
        )
        assert not ok
        assert "端口仍不可用" in messages[0]
        assert stub._service_state == TTSServiceState.FAILED

    def test_failed_state_not_cached_as_checked(self) -> None:
        """失败不缓存：_service_checked 仍为 False，下次请求重新探测。"""
        stub = _stub_provider()
        stub._service_checked = False
        stub._server_process = _ExitedProcess()
        _wait_local_service_ready(
            provider=stub,
            service_name="GPT-SoVITS",
            ready_check=lambda: False,
            fail_callback=lambda _m: None,
            timeout_seconds=3,
        )
        assert stub._service_checked is False
