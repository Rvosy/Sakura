from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.backchannel.models import (
    BackchannelManifest,
    BackchannelTemplate,
    BackchannelVariant,
)


ROOT = Path(__file__).resolve().parents[2]


class FileEngine:
    def synthesize(self, queue, request, *, fail, skip):  # type: ignore[no-untyped-def]
        _ = fail, skip
        path = queue._cache_dir / "engine-output.wav"
        path.write_bytes(b"RIFF-test-wave")
        return path


class LateFileEngine:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.path: Path | None = None

    def synthesize(self, queue, request, *, fail, skip):  # type: ignore[no-untyped-def]
        _ = request, fail, skip
        self.entered.set()
        assert self.release.wait(2)
        self.path = queue._cache_dir / "late-output.wav"
        self.path.write_bytes(b"RIFF-late-wave")
        return self.path


def _service(tmp_path: Path, engine: object):  # type: ignore[no-untyped-def]
    from app.voice.tts_synthesis_service import TTSSynthesisService

    supervisor = SimpleNamespace(
        settings=SimpleNamespace(text_lang="ja", tone_references={}),
        service_ready=True,
        ensure_ready=lambda: (True, "ready"),
    )
    return TTSSynthesisService(
        supervisor=supervisor,
        engine=engine,
        cache_dir=tmp_path / "data" / "cache" / "tts",
        resource_manager=None,
    )


def test_headless_tts_service_returns_random_session_resource(tmp_path: Path) -> None:
    service = _service(tmp_path, FileEngine())

    handle = service.synthesize("こんにちは", "温和", request_id="tts-request-1")
    result = handle.result(timeout=2)

    assert result.request_id == "tts-request-1"
    assert result.skipped_reason == ""
    assert result.resource is not None
    assert result.resource.id.startswith("audio-")
    assert result.resource.path.parent == tmp_path / "data" / "cache" / "tts"
    assert result.resource.path.name == f"{result.resource.id}.wav"
    assert result.resource.path.read_bytes() == b"RIFF-test-wave"
    assert result.resource.media_type == "audio/wav"
    assert result.resource.byte_length == len(b"RIFF-test-wave")
    private = result.resource.to_private_dto()
    assert private["path"] == str(result.resource.path.resolve())
    assert private["expiresAt"]
    service.close()


def test_tts_service_cancel_ignores_late_audio_and_cleans_file(tmp_path: Path) -> None:
    engine = LateFileEngine()
    service = _service(tmp_path, engine)
    handle = service.synthesize("遅い音声", request_id="tts-cancel")
    assert engine.entered.wait(1)

    assert service.cancel("tts-cancel") is True
    engine.release.set()

    from app.voice.tts_synthesis_service import TTSSynthesisCancelled

    with pytest.raises(TTSSynthesisCancelled):
        handle.result(timeout=2)
    assert engine.path is not None
    assert not engine.path.exists()
    service.close()


def test_tts_service_adopts_existing_backchannel_audio_into_controlled_cache(
    tmp_path: Path,
) -> None:
    source = tmp_path / "character" / "backchannel.wav"
    source.parent.mkdir()
    source.write_bytes(b"RIFF-prebuilt")
    service = _service(tmp_path, FileEngine())

    result = service.adopt_audio(source, text="うん。", tone="温和").result(timeout=1)

    assert result.resource is not None
    assert result.resource.path.parent == tmp_path / "data" / "cache" / "tts"
    assert result.resource.path.read_bytes() == b"RIFF-prebuilt"
    assert source.is_file()
    service.close()


def test_null_tts_service_skips_without_creating_files(tmp_path: Path) -> None:
    from app.voice.tts_synthesis_service import NullTTSSynthesisService

    service = NullTTSSynthesisService()
    result = service.synthesize("静音", request_id="tts-null").result(timeout=0)

    assert result.request_id == "tts-null"
    assert result.resource is None
    assert result.skipped_reason == "tts_disabled"
    assert service.cancel("tts-null") is False
    assert list(tmp_path.iterdir()) == []


def test_tts_service_close_rejects_new_requests_and_is_idempotent(tmp_path: Path) -> None:
    from app.voice.tts_synthesis_service import TTSSynthesisClosed

    service = _service(tmp_path, FileEngine())
    service.close()
    service.close()

    with pytest.raises(TTSSynthesisClosed):
        service.synthesize("late")


def test_tts_synthesis_service_import_path_has_no_qt_or_playback_module() -> None:
    code = """
import json
import os
import sys
os.environ['SAKURA_HEADLESS'] = '1'
import app.voice.tts_synthesis_service
blocked = sorted(
    name for name in sys.modules
    if name == 'PySide6' or name.startswith('PySide6.')
    or name == 'app.voice.tts_playback'
    or name == 'app.voice.tts'
)
print(json.dumps(blocked))
"""
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        [str(ROOT / "runtime" / "python.exe"), "-c", code],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert json.loads(result.stdout) == []


def test_headless_backchannel_uses_existing_classifier_and_resolver() -> None:
    from app.backchannel.classifier import RuleClassifier
    from app.backchannel.headless_service import HeadlessBackchannelService

    manifest = BackchannelManifest(
        templates=(
            BackchannelTemplate(
                id="fallback",
                tone="温和",
                portrait="smile",
                intent="fallback",
                variants=(BackchannelVariant(ja="うん。", zh="嗯。"),),
            ),
        )
    )
    settings = SimpleNamespace(
        active=True,
        probability=1.0,
        delay_ms=0,
        tts_enabled=True,
    )
    choices = []
    service = HeadlessBackchannelService(
        RuleClassifier(),
        manifest,
        settings=settings,
        on_choice=choices.append,
    )

    service.schedule("随便聊聊")

    deadline = threading.Event()
    for _ in range(100):
        if choices:
            break
        deadline.wait(0.01)
    assert choices[0].variant.zh == "嗯。"
    service.close()


def test_headless_backchannel_cancel_ignores_late_classifier_result() -> None:
    from app.backchannel.headless_service import HeadlessBackchannelService

    class BlockingClassifier:
        prefers_background = True

        def __init__(self) -> None:
            self.entered = threading.Event()
            self.release = threading.Event()

        def classify(self, _text):  # type: ignore[no-untyped-def]
            from app.backchannel.models import BackchannelLabel

            self.entered.set()
            assert self.release.wait(2)
            return BackchannelLabel("support", "sad", 0.9)

    classifier = BlockingClassifier()
    manifest = BackchannelManifest(
        templates=(
            BackchannelTemplate(
                id="support",
                tone="安慰",
                portrait="sad",
                intent="support",
                emotion="sad",
                variants=(BackchannelVariant(ja="いるよ。", zh="我在。"),),
            ),
        )
    )
    settings = SimpleNamespace(active=True, probability=1.0, delay_ms=0, tts_enabled=True)
    choices = []
    service = HeadlessBackchannelService(
        classifier,
        manifest,
        settings=settings,
        on_choice=choices.append,
    )
    service.schedule("难过")
    assert classifier.entered.wait(1)

    service.cancel()
    classifier.release.set()
    service.close()

    assert choices == []
