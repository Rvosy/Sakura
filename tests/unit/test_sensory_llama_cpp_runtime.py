from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from app.core.resource_manager import ResourceRegistry
from app.sensory.llama_cpp_runtime import (
    LLAMA_CPP_SERVER_ENV,
    LlamaCppLaunchConfig,
    LlamaCppRuntimeError,
    LlamaCppRuntimeManager,
    build_llama_server_command,
    check_llama_cpp_health,
    discover_llama_server_binary,
)


def test_build_llama_server_command_supports_hf_repo_and_runtime_tuning(tmp_path: Path) -> None:
    binary = _executable(tmp_path / "llama-server")

    command = build_llama_server_command(
        LlamaCppLaunchConfig(
            binary_path=str(binary),
            hf_repo="ggml-org/Qwen3-ASR-0.6B-GGUF",
            host="127.0.0.1",
            port=18081,
            alias="sakura-audio",
            ctx_size=8192,
            n_gpu_layers=99,
            threads=6,
            extra_args=("--no-webui",),
        )
    )

    assert command == [
        str(binary),
        "--host",
        "127.0.0.1",
        "--port",
        "18081",
        "--alias",
        "sakura-audio",
        "-c",
        "8192",
        "-hf",
        "ggml-org/Qwen3-ASR-0.6B-GGUF",
        "--n-gpu-layers",
        "99",
        "--threads",
        "6",
        "--no-webui",
    ]


def test_build_llama_server_command_supports_local_gguf_and_mmproj(tmp_path: Path) -> None:
    binary = _executable(tmp_path / "llama-server")
    model = tmp_path / "model.gguf"
    mmproj = tmp_path / "mmproj.gguf"

    command = build_llama_server_command(
        LlamaCppLaunchConfig(
            binary_path=str(binary),
            model_path=str(model),
            mmproj_path=str(mmproj),
            n_gpu_layers="auto",
        )
    )

    assert "-m" in command
    assert str(model) in command
    assert "--mmproj" in command
    assert str(mmproj) in command
    assert "--n-gpu-layers" not in command


def test_build_llama_server_command_requires_model_or_hf_repo(tmp_path: Path) -> None:
    binary = _executable(tmp_path / "llama-server")

    with pytest.raises(LlamaCppRuntimeError, match="GGUF"):
        build_llama_server_command(LlamaCppLaunchConfig(binary_path=str(binary)))


def test_discover_llama_server_binary_prefers_env_then_bundled(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    env_binary = _executable(tmp_path / "env" / "llama-server")
    bundled_binary = _executable(
        tmp_path / "data" / "local_runtimes" / "llama_cpp" / "b1" / "bin" / "llama-server"
    )

    monkeypatch.setenv(LLAMA_CPP_SERVER_ENV, str(env_binary))
    assert discover_llama_server_binary(tmp_path) == str(env_binary)

    monkeypatch.delenv(LLAMA_CPP_SERVER_ENV)
    assert discover_llama_server_binary(tmp_path) == str(bundled_binary)


def test_check_llama_cpp_health_reads_openai_models_response() -> None:
    def fake_urlopen(request: object, timeout: float) -> _FakeHTTPResponse:
        assert str(getattr(request, "full_url", "")).endswith("/v1/models")
        assert timeout == 2.0
        return _FakeHTTPResponse({"data": [{"id": "sakura-audio"}]})

    status = check_llama_cpp_health(
        "http://127.0.0.1:18080/v1",
        timeout_seconds=2.0,
        urlopen=fake_urlopen,
    )

    assert status.healthy is True
    assert status.model_id == "sakura-audio"


def test_llama_cpp_runtime_manager_starts_process_and_registers_resource(tmp_path: Path) -> None:
    binary = _executable(tmp_path / "llama-server")
    calls: list[list[str]] = []
    process = _FakeProcess(pid=4321)

    def fake_popen(args: list[str], **kwargs: Any) -> _FakeProcess:
        calls.append(list(args))
        assert kwargs["cwd"] == str(tmp_path)
        assert "SAKURA_TEST_ENV" in kwargs["env"]
        return process

    health_calls = 0

    def fake_urlopen(_request: object, timeout: float) -> _FakeHTTPResponse:
        del timeout
        nonlocal health_calls
        health_calls += 1
        if health_calls == 1:
            raise OSError("not ready")
        return _FakeHTTPResponse({"data": [{"id": "sakura-managed"}]})

    registry = ResourceRegistry()
    manager = LlamaCppRuntimeManager(
        base_dir=tmp_path,
        resource_registry=registry,
        popen_factory=fake_popen,
        urlopen=fake_urlopen,
        sleep=lambda _seconds: None,
    )

    status = manager.start(
        LlamaCppLaunchConfig(
            binary_path=str(binary),
            hf_repo="ggml-org/Qwen3-ASR-0.6B-GGUF",
            env={"SAKURA_TEST_ENV": "1"},
        )
    )

    assert status.healthy is True
    assert status.managed is True
    assert status.pid == 4321
    assert status.model_id == "sakura-managed"
    assert calls and calls[0][0] == str(binary)
    assert len(registry._resources) == 1

    assert manager.stop() is True
    assert process.terminated is True
    assert registry._resources == []


def test_llama_cpp_runtime_manager_reuses_existing_healthy_endpoint(tmp_path: Path) -> None:
    def fake_urlopen(_request: object, timeout: float) -> _FakeHTTPResponse:
        del timeout
        return _FakeHTTPResponse({"data": [{"id": "already-running"}]})

    manager = LlamaCppRuntimeManager(
        base_dir=tmp_path,
        popen_factory=lambda _args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not start")),
        urlopen=fake_urlopen,
    )

    status = manager.start(LlamaCppLaunchConfig(hf_repo="ggml-org/Qwen3-ASR-0.6B-GGUF"))

    assert status.healthy is True
    assert status.managed is False
    assert status.model_id == "already-running"


def _executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    if os.name != "nt":
        path.chmod(0o755)
    return path


class _FakeHTTPResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self) -> "_FakeHTTPResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class _FakeProcess:
    def __init__(self, *, pid: int) -> None:
        self.pid = pid
        self.terminated = False
        self.killed = False
        self._alive = True

    def poll(self) -> int | None:
        return None if self._alive else 0

    def terminate(self) -> None:
        self.terminated = True
        self._alive = False

    def kill(self) -> None:
        self.killed = True
        self._alive = False

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self._alive = False
        return 0
