"""Opt-in native inference checks; no implicit package/model downloads.

Set SAKURA_ASR_TEST_MODELS to a ModelResources root with the five public
test_wavs (zh/ja/en/yue/ko.wav) beside its version directory. Optionally set
SAKURA_ASR_TEST_DEPENDENCIES to an isolated, explicitly installed dependency root.
"""
from __future__ import annotations

import os
import json
import shutil
import re
import sys
import threading
import time
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from plugins.builtin.sakura_asr_sensevoice._resources import ModelResources
from plugins.builtin.sakura_asr_sensevoice.plugin import SenseVoiceProvider

pytestmark = pytest.mark.skipif(not os.environ.get("SAKURA_ASR_TEST_MODELS"), reason="Native ASR resources must be explicitly installed in an isolated test root")


@pytest.fixture(scope="module")
def engine():
    dependency = os.environ.get("SAKURA_ASR_TEST_DEPENDENCIES")
    if dependency:
        sys.path.insert(0, dependency)
    root = Path(os.environ["SAKURA_ASR_TEST_MODELS"])
    provider = SenseVoiceProvider(SimpleNamespace(get=lambda _: None), ModelResources(root))
    start = time.perf_counter()
    provider.warmup()
    provider.thread.join(30)
    assert provider.status()["available"], provider.status()
    print(f"\nSenseVoice cold load: {time.perf_counter() - start:.3f}s")
    yield provider, root
    provider.close()
    if dependency:
        sys.path.remove(dependency)


@pytest.mark.parametrize("language", ["zh", "ja", "en", "yue", "ko"])
def test_public_speech_samples(engine, language):
    provider, root = engine
    start = time.perf_counter()
    text, detected = provider._recognize(root / (language + ".wav"), "auto", threading.Event())
    assert text.strip() and "<|" not in text
    assert detected == language
    print(f"\n{language} hot inference: {time.perf_counter() - start:.3f}s; {len(text)} characters")


def test_silence_short_pause_low_volume_and_mixed_language(engine, tmp_path):
    provider, root = engine
    np = provider.np

    def read(name):
        with wave.open(str(root / name), "rb") as audio:
            return np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2")

    def write(name, samples):
        path = tmp_path / name
        with wave.open(str(path), "wb") as audio:
            audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            audio.writeframes(samples.astype("<i2").tobytes())
        return path

    silence = write("silence.wav", np.zeros(32000))
    with pytest.raises(ValueError, match="ASR_NO_SPEECH"):
        provider._recognize(silence, "auto", threading.Event())
    zh = read("zh.wav")
    low = write("low.wav", zh.astype(np.float32) * .1)
    assert provider._recognize(low, "zh", threading.Event())[0].strip()
    mixed = write("mixed.wav", np.concatenate([zh, np.zeros(3200), read("en.wav")]))
    text, _ = provider._recognize(mixed, "auto", threading.Event())
    assert any("\u4e00" <= c <= "\u9fff" for c in text)
    assert any("a" <= c.lower() <= "z" for c in text)


def test_reject_invalid_native_audio_before_inference(engine, tmp_path):
    provider, _ = engine
    invalid = tmp_path / "wrong-format.wav"
    with wave.open(str(invalid), "wb") as audio:
        audio.setparams((2, 2, 44100, 0, "NONE", "not compressed"))
        audio.writeframes(b"\0" * 1024)
    with pytest.raises(ValueError, match="ASR_AUDIO_INVALID"):
        provider._recognize(invalid, "auto", threading.Event())


@pytest.mark.parametrize("pause_samples", [0, 3200], ids=["contiguous", "short-pause"])
def test_long_speech_vad_boundaries_keep_every_repeated_sentence(engine, tmp_path, monkeypatch, pause_samples):
    provider, root = engine
    np = provider.np
    with wave.open(str(root / "zh.wav"), "rb") as audio:
        samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2")
    detected_spans = []
    detector = provider.sherpa.VoiceActivityDetector

    class RecordingDetector:
        def __init__(self, *args, **kwargs):
            self.delegate = detector(*args, **kwargs)

        def __getattr__(self, name):
            return getattr(self.delegate, name)

        @property
        def front(self):
            segment = self.delegate.front
            detected_spans.append((segment.start / 16000, (segment.start + len(segment.samples)) / 16000))
            return segment

    monkeypatch.setattr(provider.sherpa, "VoiceActivityDetector", RecordingDetector)
    reference, _ = provider._recognize(root / "zh.wav", "zh", threading.Event())
    # VAD distinguishes speech from leading background noise in the sample.
    # Keep its consonant context but remove long outer silence before repeating.
    phrase = samples[max(0, int(detected_spans[0][0] * 16000) - 4000):min(len(samples), int(detected_spans[-1][1] * 16000) + 800)]
    count = 6
    repeated = np.concatenate([np.concatenate([phrase, np.zeros(pause_samples, dtype=np.int16)]) for _ in range(count)])
    assert 15 * 16000 < len(repeated) <= 60 * 16000
    path = tmp_path / "repeated-zh.wav"
    with wave.open(str(path), "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        audio.writeframes(repeated.astype("<i2").tobytes())
    detected_spans.clear()
    decoded = []
    recognizer = provider.engine

    class RecordingRecognizer:
        def create_stream(self):
            return recognizer.create_stream()

        def decode_stream(self, stream):
            recognizer.decode_stream(stream)
            decoded.append(stream.result.text)

    monkeypatch.setattr(provider, "engine", RecordingRecognizer())
    text, language = provider._recognize(path, "zh", threading.Event())
    canonical = lambda value: re.sub(r"[^\w]", "", value)
    print(f"\nLong speech ({len(repeated) / 16000:.3f}s, pause {pause_samples / 16000}s): {text}")
    print(f"VAD spans: {detected_spans}")
    print(f"Decoded in {len(decoded)} bounded recognition segments")
    assert language == "zh"
    assert len(decoded) > 1
    assert canonical(text) == canonical(reference) * count


def test_real_model_through_host_hub_and_isolated_provider_process(tmp_path, monkeypatch):
    from app.agent.tools import ToolRegistry
    from app.core_host.plugin_runtime_application import PluginRuntimeApplication
    from app.plugins.inventory import PluginInventory
    from app.storage.runtime_roots import RuntimeRoots
    from plugins.builtin.sakura_asr_sensevoice._resources import VERSION
    from app.core_host import plugin_host_services

    logs = []
    monkeypatch.setattr(plugin_host_services, "log_message", lambda severity, message, **values: logs.append({"severity": severity, "message": message, **values}))

    dependency = os.environ.get("SAKURA_ASR_TEST_DEPENDENCIES")
    if not dependency:
        pytest.skip("An explicit isolated dependency directory is required for the process test")
    repository = Path(__file__).parents[2]
    distribution, user = tmp_path / "distribution", tmp_path / "user"
    user.mkdir()
    bundled = distribution / "plugins/builtin"
    bundled.mkdir(parents=True)
    for name in ("sakura_asr_hub", "sakura_asr_sensevoice"):
        shutil.copytree(repository / "plugins/builtin" / name, bundled / name, ignore=shutil.ignore_patterns("__pycache__"))

    def link_or_copy(source, target):
        try:
            os.link(source, target)
        except OSError:
            shutil.copy2(source, target)
        return target

    dependencies = distribution / "plugins/dependencies/sakura.asr.sensevoice"
    shutil.copytree(
        dependency, dependencies, copy_function=link_or_copy,
        ignore=shutil.ignore_patterns(".sakura-dependencies.json"),
    )
    (dependencies / ".sakura-dependencies.json").write_text(json.dumps({"schemaVersion": 1, "kind": "requirements.txt", "python": f"{sys.version_info.major}.{sys.version_info.minor}"}), encoding="utf-8")
    source = Path(os.environ["SAKURA_ASR_TEST_MODELS"])
    model_target = user / "data/plugins/sakura.asr.sensevoice/models" / VERSION
    shutil.copytree(source / VERSION, model_target, copy_function=link_or_copy)
    roots = RuntimeRoots(distribution, user)
    application = PluginRuntimeApplication(roots, "asr-native-process-test", ToolRegistry(), PluginInventory(roots).scan().runtime_specs)
    try:
        application.start()
        records = application.public_snapshot()["plugins"]
        assert all(item["state"] == "active" for item in records), records
        assert len({item["pid"] for item in records}) == 2
        application.call_service("sakura.asr", "warmup")
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            status = application.call_service("sakura.asr", "status")
            if status["available"]:
                break
            time.sleep(.05)
        assert status["available"], status
        identity = application.service_identity(status["serviceKey"])
        allocation = application.audio_input.allocate("real-speech", status["providerId"], status["serviceKey"], identity["scopeId"])
        shutil.copyfile(source / "zh.wav", allocation["path"])
        audio = application.audio_input.commit(allocation["resourceId"])
        started = time.perf_counter()
        result = application.call_service("sakura.asr", "begin", {"requestId": "real-speech", "audio": audio, "providerId": status["providerId"], "configVersion": status["configVersion"], "language": "auto"})
        assert result["state"] == "running", result
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            result = application.call_service("sakura.asr", "poll", "real-speech")
            if result["state"] != "running":
                break
            time.sleep(.02)
        assert result["state"] == "succeeded", result
        assert any("\u4e00" <= c <= "\u9fff" for c in result["text"])
        assert application.call_service("sakura.asr", "poll", "real-speech") == result
        assert application.audio_input.count == 0
        assert not Path(allocation["path"]).exists()
        # Native process memory is measured by the Windows process counter when
        # available; other platforms do not claim this Windows-only evidence.
        peak = None
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class Counters(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [(name, ctypes.c_size_t) for name in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            psapi = ctypes.WinDLL("psapi", use_last_error=True)
            psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
            provider_pid = next(item["pid"] for item in records if item["pluginId"] == "sakura.asr.sensevoice")
            handle = kernel.OpenProcess(0x0410, False, provider_pid)
            if handle:
                try:
                    counters = Counters()
                    counters.cb = ctypes.sizeof(counters)
                    if psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                        peak = round(counters.PeakWorkingSetSize / (1024 * 1024), 1)
                finally:
                    kernel.CloseHandle(handle)
        print(f"\nHost -> Hub -> SenseVoice -> Host complete: {time.perf_counter() - started:.3f}s; provider peak working set: {peak} MiB")
    finally:
        application.close()
    provider_logs = [item for item in logs if item.get("plugin_id") == "sakura.asr.sensevoice"]
    events = [item["fields"]["event"] for item in provider_logs]
    for event in ("asr.provider.started", "asr.model.load.started", "asr.model.load.succeeded", "asr.recognition.started", "asr.recognition.succeeded", "asr.provider.stopped"):
        assert events.count(event) == 1
    assert len(provider_logs) == 6
    finished = next(item for item in provider_logs if item["fields"]["event"] == "asr.recognition.succeeded")
    assert finished["fields"]["request_id"] == "real-speech"
    assert isinstance(finished["fields"]["duration_ms"], int)
    assert result["text"] not in json.dumps(provider_logs, ensure_ascii=False)
    assert str(allocation["path"]) not in json.dumps(provider_logs)
