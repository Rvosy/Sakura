from __future__ import annotations

import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest

from app.ui.settings import resource_tasks
from app.voice.tts_bundle import (
    GENIE_TTS,
    GPT_SOVITS_NVIDIA50,
    GPT_SOVITS_STANDARD,
    GPUInfo,
    TTSBundleDownloadProgress,
    TTSBundleEntry,
    TTSBundleInstallResult,
)


@contextmanager
def _temp_root():
    root = Path.cwd() / "temp" / f"settings-resource-{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=False)
    yield root


def _wait_for_task(manager: resource_tasks.SettingsResourceTaskManager, key: str) -> dict[str, object]:
    deadline = time.time() + 3
    while time.time() < deadline:
        task = manager.snapshot()["tasks"].get(key)
        if task and task["status"] not in {"queued", "running"}:
            return task
        time.sleep(0.02)
    raise AssertionError(f"task {key} did not finish")


def _demo_entry() -> TTSBundleEntry:
    return TTSBundleEntry(
        key="demo_tts",
        label="Demo TTS",
        filename="demo.7z",
        download_url="https://example.test/demo.7z",
        size=100,
        sha256="0" * 64,
        provider="gpt-sovits",
        supported_systems=(),
    )


def test_tts_resource_task_reports_success_and_runtime_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    entry = _demo_entry()
    monkeypatch.setattr(resource_tasks, "compatible_tts_bundles", lambda: (entry,))
    monkeypatch.setattr(resource_tasks, "recommend_tts_bundle", lambda _gpus=None: entry)

    def fake_install(
        selected: TTSBundleEntry,
        base_dir: Path,
        *,
        check_cancel,
        on_progress,
        on_status,
        on_download_progress,
    ) -> TTSBundleInstallResult:
        assert selected == entry
        on_status("download")
        on_download_progress(
            TTSBundleDownloadProgress(
                status="download",
                downloaded_bytes=50,
                total_bytes=100,
                percent=50,
                bytes_per_second=25,
            )
        )
        on_progress(90)
        return TTSBundleInstallResult(
            work_dir=base_dir / "tts" / "demo",
            provider="gpt-sovits",
            python_path=base_dir / "tts" / "demo" / "runtime" / "python.exe",
        )

    monkeypatch.setattr(resource_tasks, "install_tts_bundle", fake_install)
    with _temp_root() as root:
        manager = resource_tasks.SettingsResourceTaskManager(root)

        snapshot = manager.start_tts_install("demo_tts")
        assert snapshot["tasks"]["tts"]["status"] in {"queued", "running", "succeeded"}

        task = _wait_for_task(manager, "tts")
        assert task["status"] == "succeeded"
        assert task["result"]["provider"] == "gpt-sovits"
        assert task["result"]["work_dir"].endswith("tts\\demo") or task["result"]["work_dir"].endswith("tts/demo")
        assert task["progress"] == 100


def test_tts_resource_task_rejects_duplicate_running_task(monkeypatch: pytest.MonkeyPatch) -> None:
    entry = _demo_entry()
    monkeypatch.setattr(resource_tasks, "compatible_tts_bundles", lambda: (entry,))
    monkeypatch.setattr(resource_tasks, "recommend_tts_bundle", lambda _gpus=None: entry)

    def slow_install(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        time.sleep(0.4)
        return TTSBundleInstallResult(work_dir=Path.cwd() / "temp" / "tts" / "demo", provider="gpt-sovits")

    monkeypatch.setattr(resource_tasks, "install_tts_bundle", slow_install)
    with _temp_root() as root:
        manager = resource_tasks.SettingsResourceTaskManager(root)

        manager.start_tts_install("demo_tts")
        with pytest.raises(ValueError, match="已有资源任务"):
            manager.start_tts_install("demo_tts")
        _wait_for_task(manager, "tts")


def test_tts_resource_task_can_be_cancelled(monkeypatch: pytest.MonkeyPatch) -> None:
    entry = _demo_entry()
    monkeypatch.setattr(resource_tasks, "compatible_tts_bundles", lambda: (entry,))
    monkeypatch.setattr(resource_tasks, "recommend_tts_bundle", lambda _gpus=None: entry)

    def cancellable_install(
        _entry: TTSBundleEntry,
        _base_dir: Path,
        *,
        check_cancel,
        on_progress,
        on_status,
        on_download_progress,
    ) -> TTSBundleInstallResult:
        on_status("download")
        for _ in range(100):
            time.sleep(0.02)
            check_cancel()
        raise AssertionError("task should have been cancelled")

    monkeypatch.setattr(resource_tasks, "install_tts_bundle", cancellable_install)
    with _temp_root() as root:
        manager = resource_tasks.SettingsResourceTaskManager(root)

        manager.start_tts_install("demo_tts")
        time.sleep(0.05)
        manager.cancel_task("tts")

        task = _wait_for_task(manager, "tts")
        assert task["status"] == "cancelled"
        assert "保留" in task["detail"]


def test_memory_model_snapshot_uses_memory_store_download_state() -> None:
    class FakeMemoryStore:
        def __init__(self) -> None:
            self.needs_download = True

        def needs_embedding_model_download(self) -> bool:
            return self.needs_download

    store = FakeMemoryStore()
    with _temp_root() as root:
        manager = resource_tasks.SettingsResourceTaskManager(root)
        manager.set_memory_store(store)

        assert manager.snapshot()["memory_model"]["ready"] is False
        store.needs_download = False
        assert manager.snapshot()["memory_model"]["ready"] is True


def test_shared_manager_replaces_memory_store_with_current_window_context() -> None:
    class FakeMemoryStore:
        def needs_embedding_model_download(self) -> bool:
            return False

    with _temp_root() as root:
        manager = resource_tasks.settings_resource_task_manager(root, memory_store=FakeMemoryStore())
        assert manager.snapshot()["memory_model"]["available"] is True

        same_manager = resource_tasks.settings_resource_task_manager(root, memory_store=None)
        assert same_manager is manager
        assert same_manager.snapshot()["memory_model"]["available"] is False


def test_tts_snapshot_keeps_58gb_nvidia_as_gptsovits_capable(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.voice import tts_bundle

    monkeypatch.setattr(tts_bundle.sys, "platform", "win32")
    monkeypatch.setattr(
        resource_tasks,
        "list_nvidia_gpus",
        lambda: [GPUInfo("NVIDIA GeForce GTX 1060", 5.8)],
    )

    with _temp_root() as root:
        snapshot = resource_tasks.SettingsResourceTaskManager(root).snapshot()["tts"]

    assert snapshot["genie_key"] == GENIE_TTS.key
    assert snapshot["recommended_key"] == GPT_SOVITS_STANDARD.key
    assert snapshot["gpt_sovits_recommended_key"] == GPT_SOVITS_STANDARD.key
    assert snapshot["gpu_status"]["gpt_sovits"]["capable"] is True
    assert "5.8~5.9GB" in snapshot["gpu_status"]["gpt_sovits"]["vram_note"]
    variants = {bundle["key"]: bundle["variant"] for bundle in snapshot["bundles"]}
    assert variants[GPT_SOVITS_STANDARD.key] == "gpt-sovits-standard"
    assert variants[GPT_SOVITS_NVIDIA50.key] == "gpt-sovits-50"


def test_tts_snapshot_recommends_nvidia50_bundle_for_50_series(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.voice import tts_bundle

    monkeypatch.setattr(tts_bundle.sys, "platform", "win32")
    monkeypatch.setattr(
        resource_tasks,
        "list_nvidia_gpus",
        lambda: [GPUInfo("NVIDIA GeForce RTX 5080", 16.0)],
    )

    with _temp_root() as root:
        snapshot = resource_tasks.SettingsResourceTaskManager(root).snapshot()["tts"]

    assert snapshot["recommended_key"] == GPT_SOVITS_NVIDIA50.key
    assert snapshot["gpt_sovits_recommended_key"] == GPT_SOVITS_NVIDIA50.key
    assert "50 系" in snapshot["gpu_status"]["gpt_sovits"]["message"]


def test_tts_snapshot_warns_for_small_nvidia_gpu(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.voice import tts_bundle

    monkeypatch.setattr(tts_bundle.sys, "platform", "win32")
    monkeypatch.setattr(
        resource_tasks,
        "list_nvidia_gpus",
        lambda: [GPUInfo("NVIDIA GeForce GTX 1050 Ti", 4.0)],
    )

    with _temp_root() as root:
        snapshot = resource_tasks.SettingsResourceTaskManager(root).snapshot()["tts"]

    assert snapshot["recommended_key"] == GENIE_TTS.key
    assert snapshot["gpu_status"]["gpt_sovits"]["capable"] is False
    assert snapshot["gpu_status"]["gpt_sovits"]["severity"] == "warning"
    assert "低于 GPT-SoVITS 的 6GB" in snapshot["gpu_status"]["gpt_sovits"]["message"]
