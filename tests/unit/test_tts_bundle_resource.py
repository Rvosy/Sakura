from __future__ import annotations

import threading
import time
from pathlib import Path

from app.voice.tts_bundle import (
    DownloadCancelledError,
    TTSBundleEntry,
    TTSBundleInstallResult,
)
from app.voice.tts_bundle_resource import TTSBundleResource


ENTRY = TTSBundleEntry(
    key="fixture",
    label="Fixture TTS",
    filename="fixture.7z",
    size=100,
    sha256="0" * 64,
    supported_systems=(),
)


def _wait_terminal(resource: TTSBundleResource) -> dict[str, object]:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        value = resource.load()["bundleResource"]
        if value["taskState"] not in {"queued", "running"}:
            return value
        time.sleep(0.01)
    raise AssertionError("resource did not reach a terminal state")


def test_custom_and_unsupported_resources_never_offer_network_actions(tmp_path: Path) -> None:
    custom = TTSBundleResource(
        user_root=tmp_path,
        config_get=lambda: {"endpointMode": "custom"},
        config_update=lambda _values: None,
        entry=lambda: ENTRY,
        custom_endpoint=lambda values: values.get("endpointMode") == "custom",
    )
    value = custom.load()["bundleResource"]
    assert value["applicability"] == "not_required"
    assert value["availableActionIds"] == []

    unsupported = TTSBundleResource(
        user_root=tmp_path,
        config_get=lambda: {},
        config_update=lambda _values: None,
        entry=lambda: None,
        custom_endpoint=lambda _values: False,
    )
    value = unsupported.load()["bundleResource"]
    assert value["applicability"] == "unsupported"
    assert value["availableActionIds"] == []


def test_install_updates_provider_paths_and_duplicate_click_is_idempotent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    installed = False
    release = threading.Event()
    patches: list[dict[str, object]] = []

    def fake_installer(entry, root, **callbacks):
        nonlocal installed
        callbacks["on_status"]("下载组件")
        callbacks["on_progress"](42)
        release.wait(1)
        installed = True
        return TTSBundleInstallResult(
            work_dir=root / "tts" / "fixture",
            provider=entry.provider,
            python_path=root / "tts" / "fixture" / "python.exe",
            tts_config_path=root / "tts" / "fixture" / "tts.yaml",
        )

    monkeypatch.setattr(
        "app.voice.tts_bundle_resource.is_tts_bundle_installed",
        lambda _entry, _root: installed,
    )
    resource = TTSBundleResource(
        user_root=tmp_path,
        config_get=lambda: {"endpointMode": "managed"},
        config_update=lambda values: patches.append(dict(values)),
        entry=lambda: ENTRY,
        custom_endpoint=lambda _values: False,
        installer=fake_installer,
    )
    assert resource.start({})["message"] == "已开始安装组件。"
    assert resource.start({})["message"] == "组件安装已在进行中。"
    release.set()
    value = _wait_terminal(resource)
    assert value["ready"] is True, value
    assert value["availableActionIds"] == []
    assert set(patches[0]) == {"workDir", "pythonPath", "ttsConfigPath"}
    resource.close()


def test_failed_install_retries_and_close_cancels_and_joins(tmp_path: Path) -> None:
    attempts = 0
    cancelled = threading.Event()

    def fake_installer(_entry, _root, **callbacks):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("fixture failure")
        while True:
            try:
                callbacks["check_cancel"]()
            except DownloadCancelledError:
                cancelled.set()
                raise
            time.sleep(0.01)

    resource = TTSBundleResource(
        user_root=tmp_path,
        config_get=lambda: {},
        config_update=lambda _values: None,
        entry=lambda: ENTRY,
        custom_endpoint=lambda _values: False,
        installer=fake_installer,
    )
    resource.start({})
    failed = _wait_terminal(resource)
    assert failed["taskState"] == "failed"
    assert failed["availableActionIds"] == ["retryBundle"]
    resource.start({})
    deadline = time.monotonic() + 1
    while resource.load()["bundleResource"]["taskState"] != "running" and time.monotonic() < deadline:
        time.sleep(0.01)
    resource.close()
    assert cancelled.wait(1)
    assert resource.load()["bundleResource"]["taskState"] == "cancelled"
