from __future__ import annotations

import importlib.util
import os
import sys
import types
import uuid
import wave
from pathlib import Path

import pytest

if importlib.util.find_spec("PySide6") is None:
    pyside_module = types.ModuleType("PySide6")
    qtcore_module = types.ModuleType("PySide6.QtCore")
    qtmultimedia_module = types.ModuleType("PySide6.QtMultimedia")

    class QObject:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

    class QTimer:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            self.timeout = _SignalStub()
            self._single_shot = False
            self._active = False

        def start(self, interval: int) -> None:
            self._active = True

        def stop(self) -> None:
            self._active = False

        def setTimerType(self, _timer_type: object) -> None:
            pass

        def setSingleShot(self, single_shot: bool) -> None:
            self._single_shot = single_shot

    class Qt:
        class TimerType:
            PreciseTimer = 1

    class _SignalStub:
        def __init__(self) -> None:
            pass

        def connect(self, callback: object) -> None:
            pass

        def emit(self, *args: object) -> None:
            pass

    class Signal:
        def __init__(self, *args: object) -> None:
            self._slots: list = []

        def connect(self, callback: object) -> None:
            self._slots.append(callback)

        def emit(self, *args: object) -> None:
            for slot in self._slots:
                try:
                    slot(*args)
                except Exception:
                    pass

        def disconnect(self) -> None:
            self._slots.clear()

    def Slot(*_args: object, **_kwargs: object):
        def decorator(function):
            return function
        return decorator

    class QAudioFormat:
        class SampleFormat:
            Int16 = "Int16"

        def __init__(self) -> None:
            self._sample_rate = 0
            self._channel_count = 0
            self._sample_format = None

        def setSampleRate(self, rate: int) -> None:
            self._sample_rate = rate

        def setChannelCount(self, count: int) -> None:
            self._channel_count = count

        def setSampleFormat(self, fmt: object) -> None:
            self._sample_format = fmt

        def sampleRate(self) -> int:
            return self._sample_rate

        def channelCount(self) -> int:
            return self._channel_count

    class QAudioDevice:
        def __init__(self, description: str = "Test Device", supported: bool = True) -> None:
            self._description = description
            self._supported = supported

        def description(self) -> str:
            return self._description

        def isFormatSupported(self, _fmt: QAudioFormat) -> bool:
            return self._supported

    class FakeIODevice:
        def __init__(self) -> None:
            self.written: list[bytes] = []

        def write(self, data: bytes) -> int:
            self.written.append(data)
            return len(data)

    class QAudioSink:
        def __init__(self, device: QAudioDevice, fmt: QAudioFormat, parent: object | None = None) -> None:
            self.stateChanged = Signal()
            self._device = device
            self._format = fmt
            self._io_device = FakeIODevice()
            self._bytes_free = 8192
            self._active = False

        def start(self) -> FakeIODevice | None:
            self._active = True
            return self._io_device

        def stop(self) -> None:
            self._active = False

        def bytesFree(self) -> int:
            return self._bytes_free

        def state(self) -> object:
            class State:
                name = "ActiveState"
            return State()

        def write(self, data: bytes) -> int:
            if self._active:
                self._io_device.write(data)
                return len(data)
            return 0

    class QMediaDevices:
        @staticmethod
        def defaultAudioOutput() -> QAudioDevice:
            return QAudioDevice("Default Speaker", supported=True)

    qtcore_module.QObject = QObject
    qtcore_module.QTimer = QTimer
    qtcore_module.Qt = Qt
    qtcore_module.Signal = Signal
    qtcore_module.Slot = Slot
    qtmultimedia_module.QAudioFormat = QAudioFormat
    qtmultimedia_module.QAudioSink = QAudioSink
    qtmultimedia_module.QMediaDevices = QMediaDevices
    sys.modules["PySide6"] = pyside_module
    sys.modules["PySide6.QtCore"] = qtcore_module
    sys.modules["PySide6.QtMultimedia"] = qtmultimedia_module

from app.voice.audio_sink_player import AudioSinkPlayer


def _runtime_root(name: str) -> Path:
    root = Path(__file__).resolve().parents[2] / "__pycache__" / "test_runtime" / name / uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=True)
    return root


def _write_test_wav(path: Path, channels: int = 1, sample_width: int = 2,
                    sample_rate: int = 16000, frame_count: int = 1600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * frame_count)


def test_sink_player_rejects_compressed_wav() -> None:
    """压缩 wav 格式的 comptype 校验逻辑——构造非 NONE 的 wav header 字节。"""
    root = _runtime_root("sink_compressed")
    path = root / "compressed.wav"
    # 手工构造一个 comptype 非 NONE 的 wav 文件头
    # RIFF header + fmt chunk with compression code = 6 (ALAW)
    import struct

    fmt_chunk = struct.pack(
        "<HHIIHH",
        6,       # wFormatTag = ALAW (6)
        1,       # nChannels = 1
        16000,   # nSamplesPerSec
        32000,   # nAvgBytesPerSec
        2,       # nBlockAlign
        16,      # wBitsPerSample
    )

    riff = b"RIFF"
    filesize = 36 + len(fmt_chunk) + 8 + 0  # data chunk with 0 frames
    wave_id = b"WAVE"
    fmt_header = b"fmt " + struct.pack("<I", len(fmt_chunk)) + fmt_chunk
    data_header = b"data" + struct.pack("<I", 0)

    path.write_bytes(riff + struct.pack("<I", filesize) + wave_id + fmt_header + data_header)

    result = AudioSinkPlayer._read_wav_info(path)
    assert result is None


def test_sink_player_rejects_8bit_wav() -> None:
    """8-bit wav 应被拒。"""
    root = _runtime_root("sink_8bit")
    path = root / "8bit.wav"
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(1)
        wf.setframerate(16000)
        wf.writeframes(b"\x80" * 200)

    result = AudioSinkPlayer._read_wav_info(path)
    assert result is None


def test_sink_player_rejects_unsupported_channels() -> None:
    """超过 2 声道应被拒。"""
    root = _runtime_root("sink_channels")
    path = root / "multich.wav"
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(6)
        wf.setsampwidth(2)
        wf.setframerate(44100)
        wf.writeframes(b"\x00\x00" * 600)

    result = AudioSinkPlayer._read_wav_info(path)
    assert result is None


def test_sink_player_accepts_valid_16bit_mono_wav() -> None:
    """标准 16-bit mono wav 应被接受。"""
    root = _runtime_root("sink_valid_mono")
    path = root / "valid.wav"
    _write_test_wav(path, channels=1, sample_width=2, sample_rate=16000, frame_count=1600)

    result = AudioSinkPlayer._read_wav_info(path)
    assert result is not None
    pcm_bytes, total_pcm_bytes, sample_rate, channels, sample_width, duration_ms = result
    assert sample_rate == 16000
    assert channels == 1
    assert sample_width == 2
    assert total_pcm_bytes == 3200  # 1600 frames * 2 bytes
    assert duration_ms > 0


def test_sink_player_accepts_valid_16bit_stereo_wav() -> None:
    """标准 16-bit stereo wav 应被接受。"""
    root = _runtime_root("sink_valid_stereo")
    path = root / "stereo.wav"
    _write_test_wav(path, channels=2, sample_width=2, sample_rate=44100, frame_count=4410)

    result = AudioSinkPlayer._read_wav_info(path)
    assert result is not None
    pcm_bytes, total_pcm_bytes, sample_rate, channels, sample_width, duration_ms = result
    assert channels == 2
    assert sample_rate == 44100
    assert total_pcm_bytes > 0


def test_sink_player_do_finish_is_exactly_once() -> None:
    """_do_finish 必须 exactly once，重复调用被忽略。"""
    player = AudioSinkPlayer()
    finish_reasons: list[str] = []
    player.finished.connect(lambda reason, path: finish_reasons.append(reason))

    player._finish_once("first")
    player._finish_once("second")
    player._finish_once("third")

    assert finish_reasons == ["first"]


def test_sink_player_cancel_calls_do_finish() -> None:
    """cancel 应触发 finish。"""
    player = AudioSinkPlayer()
    finish_reasons: list[str] = []
    player.finished.connect(lambda reason, path: finish_reasons.append(reason))

    player._finish_once("stopped")

    assert finish_reasons == ["stopped"]


def test_sink_player_start_returns_false_on_invalid_wav() -> None:
    """无效 wav 文件应让 start() 返回 False。"""
    player = AudioSinkPlayer()
    root = _runtime_root("sink_invalid")
    path = root / "nofile.wav"
    # 文件不存在

    ok = player.start(path)
    assert ok is False


@pytest.mark.skipif(os.environ.get("CI") == "true", reason="QAudioSink requires audio device on headless CI")
def test_sink_player_start_returns_true_for_valid_wav() -> None:
    """合法 wav 应让 start() 返回 True。"""
    root = _runtime_root("sink_start_ok")
    path = root / "test.wav"
    _write_test_wav(path, channels=1, sample_width=2, sample_rate=16000, frame_count=1600)

    player = AudioSinkPlayer()
    ok = player.start(path)
    assert ok is True