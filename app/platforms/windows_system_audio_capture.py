from __future__ import annotations

import argparse
import ctypes
import os
import struct
import sys
import time
import uuid
import wave
from ctypes import wintypes
from pathlib import Path


HRESULT = ctypes.c_long
REFERENCE_TIME_PER_SECOND = 10_000_000
CLSCTX_ALL = 0x17
E_RENDER = 0
E_CONSOLE = 0
AUDCLNT_SHAREMODE_SHARED = 0
AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
AUDCLNT_BUFFERFLAGS_SILENT = 0x2
WAVE_FORMAT_PCM = 0x0001
WAVE_FORMAT_IEEE_FLOAT = 0x0003
WAVE_FORMAT_EXTENSIBLE = 0xFFFE
SUBTYPE_PCM = "00000001-0000-0010-8000-00aa00389b71"
SUBTYPE_IEEE_FLOAT = "00000003-0000-0010-8000-00aa00389b71"
WINFUNCTYPE = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)


class CaptureError(RuntimeError):
    pass


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def from_string(cls, value: str) -> "GUID":
        raw = uuid.UUID(value).bytes_le
        data1, data2, data3 = struct.unpack("<IHH", raw[:8])
        data4 = (ctypes.c_ubyte * 8).from_buffer_copy(raw[8:])
        return cls(data1, data2, data3, data4)

    def as_uuid(self) -> uuid.UUID:
        return uuid.UUID(bytes_le=bytes(self))


class WAVEFORMATEX(ctypes.Structure):
    _fields_ = [
        ("wFormatTag", wintypes.WORD),
        ("nChannels", wintypes.WORD),
        ("nSamplesPerSec", wintypes.DWORD),
        ("nAvgBytesPerSec", wintypes.DWORD),
        ("nBlockAlign", wintypes.WORD),
        ("wBitsPerSample", wintypes.WORD),
        ("cbSize", wintypes.WORD),
    ]


class WAVEFORMATEXTENSIBLE(ctypes.Structure):
    _fields_ = [
        ("Format", WAVEFORMATEX),
        ("wValidBitsPerSample", wintypes.WORD),
        ("dwChannelMask", wintypes.DWORD),
        ("SubFormat", GUID),
    ]


CLSID_MMDEVICE_ENUMERATOR = GUID.from_string("bcde0395-e52f-467c-8e3d-c4579291692e")
IID_IMMDEVICE_ENUMERATOR = GUID.from_string("a95664d2-9614-4f35-a746-de8db63617e6")
IID_IAUDIO_CLIENT = GUID.from_string("1cb9ad4c-dbfa-4c32-b178-c2f568a703b2")
IID_IAUDIO_CAPTURE_CLIENT = GUID.from_string("c8adbd64-e71e-48a0-a4de-185c395cd317")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--duration", type=float, default=3.0)
    parser.add_argument("--sample-rate", type=int, default=16000)
    parser.add_argument("--channels", type=int, default=1)
    args = parser.parse_args()

    if os.name != "nt":
        raise CaptureError("WASAPI loopback capture requires Windows")
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        output.unlink()
    except FileNotFoundError:
        pass
    capture_loopback(
        output=output,
        duration_seconds=max(0.5, min(10.0, float(args.duration))),
        target_sample_rate=max(8000, min(48000, int(args.sample_rate))),
        target_channels=max(1, min(2, int(args.channels))),
    )
    return 0


def capture_loopback(
    *,
    output: Path,
    duration_seconds: float,
    target_sample_rate: int,
    target_channels: int,
) -> None:
    ole32 = ctypes.OleDLL("ole32")
    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole32.CoInitializeEx.restype = HRESULT
    ole32.CoUninitialize.argtypes = []
    ole32.CoUninitialize.restype = None
    ole32.CoCreateInstance.argtypes = [
        ctypes.POINTER(GUID),
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(GUID),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    ole32.CoCreateInstance.restype = HRESULT
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole32.CoTaskMemFree.restype = None

    enumerator = ctypes.c_void_p()
    device = ctypes.c_void_p()
    audio_client = ctypes.c_void_p()
    capture_client = ctypes.c_void_p()
    mix_format_ptr = ctypes.c_void_p()
    initialized = False
    try:
        _check_hresult(ole32.CoInitializeEx(None, 0))
        initialized = True
        _check_hresult(
            ole32.CoCreateInstance(
                ctypes.byref(CLSID_MMDEVICE_ENUMERATOR),
                None,
                CLSCTX_ALL,
                ctypes.byref(IID_IMMDEVICE_ENUMERATOR),
                ctypes.byref(enumerator),
            )
        )
        get_default_audio_endpoint = _com_method(
            enumerator,
            4,
            HRESULT,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_void_p),
        )
        _check_hresult(get_default_audio_endpoint(enumerator, E_RENDER, E_CONSOLE, ctypes.byref(device)))

        activate = _com_method(
            device,
            3,
            HRESULT,
            ctypes.POINTER(GUID),
            wintypes.DWORD,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        )
        _check_hresult(
            activate(
                device,
                ctypes.byref(IID_IAUDIO_CLIENT),
                CLSCTX_ALL,
                None,
                ctypes.byref(audio_client),
            )
        )

        get_mix_format = _com_method(audio_client, 8, HRESULT, ctypes.POINTER(ctypes.c_void_p))
        _check_hresult(get_mix_format(audio_client, ctypes.byref(mix_format_ptr)))
        wave_format = ctypes.cast(mix_format_ptr, ctypes.POINTER(WAVEFORMATEX)).contents
        _validate_wave_format(wave_format)

        initialize = _com_method(
            audio_client,
            3,
            HRESULT,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_longlong,
            ctypes.c_longlong,
            ctypes.c_void_p,
            ctypes.c_void_p,
        )
        _check_hresult(
            initialize(
                audio_client,
                AUDCLNT_SHAREMODE_SHARED,
                AUDCLNT_STREAMFLAGS_LOOPBACK,
                REFERENCE_TIME_PER_SECOND,
                0,
                mix_format_ptr,
                None,
            )
        )
        get_service = _com_method(
            audio_client,
            14,
            HRESULT,
            ctypes.POINTER(GUID),
            ctypes.POINTER(ctypes.c_void_p),
        )
        _check_hresult(get_service(audio_client, ctypes.byref(IID_IAUDIO_CAPTURE_CLIENT), ctypes.byref(capture_client)))
        start = _com_method(audio_client, 10, HRESULT)
        stop = _com_method(audio_client, 11, HRESULT)
        _check_hresult(start(audio_client))
        try:
            pcm = _read_capture_frames(
                capture_client,
                wave_format_ptr,
                duration_seconds=duration_seconds,
                target_channels=target_channels,
            )
        finally:
            stop(audio_client)
        if not pcm:
            raise CaptureError("no system audio samples were captured")
        output_sample_rate = int(wave_format.nSamplesPerSec)
        if target_sample_rate != output_sample_rate:
            pcm = _resample_pcm16(
                pcm,
                source_rate=output_sample_rate,
                target_rate=target_sample_rate,
                channels=target_channels,
            )
            output_sample_rate = target_sample_rate
        with wave.open(str(output), "wb") as wav_file:
            wav_file.setnchannels(target_channels)
            wav_file.setsampwidth(2)
            wav_file.setframerate(output_sample_rate)
            wav_file.writeframes(pcm)
    finally:
        if mix_format_ptr:
            ole32.CoTaskMemFree(mix_format_ptr)
        for pointer in (capture_client, audio_client, device, enumerator):
            _release(pointer)
        if initialized:
            ole32.CoUninitialize()


def _read_capture_frames(
    capture_client: ctypes.c_void_p,
    wave_format_ptr: ctypes.c_void_p,
    *,
    duration_seconds: float,
    target_channels: int,
) -> bytes:
    get_next_packet_size = _com_method(capture_client, 5, HRESULT, ctypes.POINTER(wintypes.UINT))
    get_buffer = _com_method(
        capture_client,
        3,
        HRESULT,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.UINT),
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(ctypes.c_ulonglong),
        ctypes.POINTER(ctypes.c_ulonglong),
    )
    release_buffer = _com_method(capture_client, 4, HRESULT, wintypes.UINT)
    wave_format = ctypes.cast(wave_format_ptr, ctypes.POINTER(WAVEFORMATEX)).contents
    output = bytearray()
    deadline = time.monotonic() + duration_seconds
    while time.monotonic() < deadline:
        packet_size = wintypes.UINT()
        _check_hresult(get_next_packet_size(capture_client, ctypes.byref(packet_size)))
        if packet_size.value == 0:
            time.sleep(0.01)
            continue
        while packet_size.value:
            data_ptr = ctypes.c_void_p()
            frame_count = wintypes.UINT()
            flags = wintypes.DWORD()
            device_position = ctypes.c_ulonglong()
            qpc_position = ctypes.c_ulonglong()
            _check_hresult(
                get_buffer(
                    capture_client,
                    ctypes.byref(data_ptr),
                    ctypes.byref(frame_count),
                    ctypes.byref(flags),
                    ctypes.byref(device_position),
                    ctypes.byref(qpc_position),
                )
            )
            try:
                byte_count = int(frame_count.value) * int(wave_format.nBlockAlign)
                if flags.value & AUDCLNT_BUFFERFLAGS_SILENT:
                    raw = b"\x00" * byte_count
                else:
                    raw = ctypes.string_at(data_ptr, byte_count)
                output.extend(_convert_to_pcm16(raw, wave_format_ptr, target_channels))
            finally:
                _check_hresult(release_buffer(capture_client, frame_count))
            _check_hresult(get_next_packet_size(capture_client, ctypes.byref(packet_size)))
    return bytes(output)


def _convert_to_pcm16(raw: bytes, wave_format_ptr: ctypes.c_void_p, target_channels: int) -> bytes:
    wave_format = ctypes.cast(wave_format_ptr, ctypes.POINTER(WAVEFORMATEX)).contents
    native_channels = max(1, int(wave_format.nChannels))
    block_align = int(wave_format.nBlockAlign)
    bits_per_sample = int(wave_format.wBitsPerSample)
    bytes_per_sample = max(1, bits_per_sample // 8)
    format_tag = _effective_format_tag(wave_format_ptr)
    if block_align <= 0 or len(raw) < block_align:
        return b""
    output = bytearray()
    for frame_offset in range(0, len(raw) - block_align + 1, block_align):
        samples: list[float] = []
        for channel in range(native_channels):
            sample_offset = frame_offset + channel * bytes_per_sample
            sample = raw[sample_offset : sample_offset + bytes_per_sample]
            samples.append(_sample_to_float(sample, bits_per_sample, format_tag))
        if target_channels == 1:
            values = [sum(samples) / len(samples)]
        elif len(samples) >= 2:
            values = samples[:2]
        else:
            values = [samples[0], samples[0]]
        for value in values:
            output.extend(struct.pack("<h", _float_to_i16(value)))
    return bytes(output)


def _sample_to_float(sample: bytes, bits_per_sample: int, format_tag: int) -> float:
    if format_tag == WAVE_FORMAT_IEEE_FLOAT and bits_per_sample == 32 and len(sample) >= 4:
        return float(struct.unpack("<f", sample[:4])[0])
    if format_tag not in {WAVE_FORMAT_PCM, WAVE_FORMAT_EXTENSIBLE}:
        return 0.0
    if bits_per_sample == 8 and sample:
        return (sample[0] - 128) / 128.0
    if bits_per_sample == 16 and len(sample) >= 2:
        return struct.unpack("<h", sample[:2])[0] / 32768.0
    if bits_per_sample == 24 and len(sample) >= 3:
        value = int.from_bytes(sample[:3] + (b"\xff" if sample[2] & 0x80 else b"\x00"), "little", signed=True)
        return value / 8388608.0
    if bits_per_sample == 32 and len(sample) >= 4:
        return struct.unpack("<i", sample[:4])[0] / 2147483648.0
    return 0.0


def _float_to_i16(value: float) -> int:
    clamped = max(-1.0, min(1.0, value))
    if clamped >= 1.0:
        return 32767
    return int(clamped * 32768)


def _resample_pcm16(pcm: bytes, *, source_rate: int, target_rate: int, channels: int) -> bytes:
    if source_rate <= 0 or target_rate <= 0 or source_rate == target_rate:
        return pcm
    frame_width = 2 * channels
    source_frame_count = len(pcm) // frame_width
    if source_frame_count <= 1:
        return pcm
    samples = struct.unpack("<" + ("h" * source_frame_count * channels), pcm[: source_frame_count * frame_width])
    target_frame_count = max(1, int(round(source_frame_count * target_rate / source_rate)))
    output = bytearray(target_frame_count * frame_width)
    for target_frame in range(target_frame_count):
        source_position = target_frame * source_rate / target_rate
        left_index = int(source_position)
        right_index = min(left_index + 1, source_frame_count - 1)
        fraction = source_position - left_index
        for channel in range(channels):
            left = samples[left_index * channels + channel]
            right = samples[right_index * channels + channel]
            value = int(round(left + (right - left) * fraction))
            struct.pack_into("<h", output, (target_frame * channels + channel) * 2, value)
    return bytes(output)


def _effective_format_tag(wave_format_ptr: ctypes.c_void_p) -> int:
    wave_format = ctypes.cast(wave_format_ptr, ctypes.POINTER(WAVEFORMATEX)).contents
    if wave_format.wFormatTag != WAVE_FORMAT_EXTENSIBLE:
        return int(wave_format.wFormatTag)
    extensible = ctypes.cast(wave_format_ptr, ctypes.POINTER(WAVEFORMATEXTENSIBLE)).contents
    subtype = str(extensible.SubFormat.as_uuid()).lower()
    if subtype == SUBTYPE_IEEE_FLOAT:
        return WAVE_FORMAT_IEEE_FLOAT
    if subtype == SUBTYPE_PCM:
        return WAVE_FORMAT_PCM
    return WAVE_FORMAT_EXTENSIBLE


def _validate_wave_format(wave_format: WAVEFORMATEX) -> None:
    if wave_format.nChannels <= 0 or wave_format.nSamplesPerSec <= 0 or wave_format.nBlockAlign <= 0:
        raise CaptureError("invalid WASAPI mix format")
    if wave_format.wFormatTag not in {WAVE_FORMAT_PCM, WAVE_FORMAT_IEEE_FLOAT, WAVE_FORMAT_EXTENSIBLE}:
        raise CaptureError(f"unsupported WASAPI format: {wave_format.wFormatTag}")


def _com_method(pointer: ctypes.c_void_p, index: int, restype: object, *argtypes: object) -> object:
    vtable = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtable[index])


def _release(pointer: ctypes.c_void_p) -> None:
    if not pointer:
        return
    release = _com_method(pointer, 2, ctypes.c_ulong)
    release(pointer)


def _check_hresult(result: int) -> None:
    if int(result) < 0:
        raise CaptureError(f"HRESULT 0x{int(result) & 0xFFFFFFFF:08X}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
