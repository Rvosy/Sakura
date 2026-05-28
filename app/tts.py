from __future__ import annotations

import json
import os
import tempfile
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from PySide6.QtCore import QObject, QUrl, Signal, Slot
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

from app.chat_reply import DEFAULT_TONE


class TTSProvider(Protocol):
    def speak(self, text: str, tone: str | None = None) -> None:
        """播放或提交一段待朗读文本。"""


class NullTTSProvider:
    def speak(self, text: str, tone: str | None = None) -> None:
        # GPT-SoVITS 接入前保留调用点，避免聊天流程以后再改。
        _ = text
        _ = tone


class TTSConfigError(RuntimeError):
    """TTS 配置缺失或格式错误。"""


@dataclass(frozen=True)
class ToneReference:
    tone: str
    ref_audio_path: Path
    ref_text: str
    ref_lang: str


@dataclass(frozen=True)
class GPTSoVITSTTSSettings:
    enabled: bool
    api_url: str
    ref_audio_path: Path
    ref_text: str
    ref_lang: str = "ja"
    text_lang: str = "ja"
    timeout_seconds: int = 60
    tone_references: dict[str, list[ToneReference]] = field(default_factory=dict)

    @classmethod
    def load(cls, env_path: Path, base_dir: Path) -> "GPTSoVITSTTSSettings":
        values = _load_env_file(env_path)
        enabled = _is_enabled(_get_env_value(values, "TTS_ENABLED", "false"))

        ref_audio_text = _get_env_value(
            values,
            "GPT_SOVITS_REF_AUDIO_PATH",
            str(base_dir / "ref" / "VO01_2210.ogg"),
        )
        ref_text_path_text = _get_env_value(
            values,
            "GPT_SOVITS_REF_TEXT_PATH",
            str(base_dir / "ref" / "text.txt"),
        )
        ref_text = _get_env_value(values, "GPT_SOVITS_REF_TEXT", "")
        tone_ref_path_text = _get_env_value(
            values,
            "GPT_SOVITS_TONE_REF_PATH",
            str(base_dir / "ref" / "ref.txt"),
        )

        ref_audio_path = _resolve_path(ref_audio_text, base_dir)
        ref_text_path = _resolve_path(ref_text_path_text, base_dir)
        tone_ref_path = _resolve_path(tone_ref_path_text, base_dir)
        if not ref_text and ref_text_path.exists():
            ref_text = ref_text_path.read_text(encoding="utf-8").strip()

        timeout_text = _get_env_value(values, "GPT_SOVITS_TIMEOUT_SECONDS", "60")
        try:
            timeout_seconds = int(timeout_text)
        except ValueError:
            timeout_seconds = 60

        settings = cls(
            enabled=enabled,
            api_url=_get_env_value(
                values,
                "GPT_SOVITS_API_URL",
                "http://127.0.0.1:9880/tts",
            ).strip(),
            ref_audio_path=ref_audio_path,
            ref_text=ref_text.strip(),
            ref_lang=_get_env_value(values, "GPT_SOVITS_REF_LANG", "ja").strip(),
            text_lang=_get_env_value(values, "GPT_SOVITS_TEXT_LANG", "ja").strip(),
            timeout_seconds=timeout_seconds,
            tone_references=_load_tone_references(tone_ref_path, base_dir),
        )
        if settings.enabled:
            settings.validate()
        return settings

    def validate(self) -> None:
        if not self.api_url:
            raise TTSConfigError("缺少 GPT_SOVITS_API_URL。")
        if self.tone_references:
            for references in self.tone_references.values():
                for reference in references:
                    if not reference.ref_audio_path.exists():
                        raise TTSConfigError(f"语气参考音频不存在：{reference.ref_audio_path}")
                    if not reference.ref_text:
                        raise TTSConfigError(f"语气参考文本为空：{reference.ref_audio_path}")
                    if not reference.ref_lang:
                        raise TTSConfigError(f"语气参考语言为空：{reference.ref_audio_path}")
        else:
            if not self.ref_audio_path.exists():
                raise TTSConfigError(f"参考音频不存在：{self.ref_audio_path}")
            if not self.ref_text:
                raise TTSConfigError("缺少参考文本，请配置 GPT_SOVITS_REF_TEXT 或 GPT_SOVITS_REF_TEXT_PATH。")
        if not self.ref_lang:
            raise TTSConfigError("缺少 GPT_SOVITS_REF_LANG。")
        if not self.text_lang:
            raise TTSConfigError("缺少 GPT_SOVITS_TEXT_LANG。")


class GPTSoVITSTTSProvider(QObject):
    _audio_ready = Signal(str)
    _failed = Signal(str)

    def __init__(self, settings: GPTSoVITSTTSSettings) -> None:
        super().__init__()
        settings.validate()
        self.settings = settings
        self._pending_audio: list[Path] = []
        self._current_audio: Path | None = None
        self._request_lock = threading.Lock()
        self._pending_requests: list[tuple[str, str | None]] = []
        self._request_running = False
        self._tone_indices: dict[str, int] = {}

        self._audio_output = QAudioOutput(self)
        self._player = QMediaPlayer(self)
        self._player.setAudioOutput(self._audio_output)
        self._player.mediaStatusChanged.connect(self._handle_media_status)
        self._player.errorOccurred.connect(self._handle_player_error)
        self._audio_ready.connect(self._enqueue_audio)
        self._failed.connect(self._log_error)

    def speak(self, text: str, tone: str | None = None) -> None:
        text = text.strip()
        if not text:
            return
        with self._request_lock:
            self._pending_requests.append((text, tone))
        self._start_next_request()

    def _start_next_request(self) -> None:
        with self._request_lock:
            if self._request_running or not self._pending_requests:
                return
            text, tone = self._pending_requests.pop(0)
            self._request_running = True

        thread = threading.Thread(
            target=self._request_audio,
            args=(text, tone),
            daemon=True,
        )
        thread.start()

    def _request_audio(self, text: str, tone: str | None) -> None:
        reference = self._select_reference(tone)
        payload = {
            "text": text,
            "text_lang": self.settings.text_lang,
            "ref_audio_path": str(reference.ref_audio_path),
            "prompt_text": reference.ref_text,
            "prompt_lang": reference.ref_lang,
            "text_split_method": "cut5",
            "batch_size": 1,
            "media_type": "wav",
            "streaming_mode": False,
            "top_k": 15,
            "top_p": 1,
            "temperature": 1, 
            "repetition_penalty": 1.2,
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url=self.settings.api_url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )

        try:
            try:
                with urllib.request.urlopen(
                    request,
                    timeout=self.settings.timeout_seconds,
                ) as response:
                    audio_data = response.read()
            except urllib.error.HTTPError as exc:
                error_body = exc.read().decode("utf-8", errors="replace")
                self._failed.emit(f"GPT-SoVITS HTTP {exc.code}: {error_body}")
                return
            except urllib.error.URLError as exc:
                self._failed.emit(f"GPT-SoVITS 请求失败：{exc.reason}")
                return
            except TimeoutError:
                self._failed.emit("GPT-SoVITS 请求超时。")
                return

            if not audio_data:
                self._failed.emit("GPT-SoVITS 返回了空音频。")
                return

            with tempfile.NamedTemporaryFile(
                prefix="sakura_tts_",
                suffix=".wav",
                delete=False,
            ) as audio_file:
                audio_file.write(audio_data)
                audio_path = audio_file.name
            self._audio_ready.emit(audio_path)
        finally:
            with self._request_lock:
                self._request_running = False
            self._start_next_request()

    def _select_reference(self, tone: str | None) -> ToneReference:
        tone_key = (tone or DEFAULT_TONE).strip() or DEFAULT_TONE
        references = self.settings.tone_references.get(tone_key)
        if not references:
            references = self.settings.tone_references.get(DEFAULT_TONE)
        if not references:
            return ToneReference(
                tone=DEFAULT_TONE,
                ref_audio_path=self.settings.ref_audio_path,
                ref_text=self.settings.ref_text,
                ref_lang=self.settings.ref_lang,
            )

        index = self._tone_indices.get(tone_key, 0) % len(references)
        self._tone_indices[tone_key] = index + 1
        return references[index]

    @Slot(str)
    def _enqueue_audio(self, audio_path: str) -> None:
        self._pending_audio.append(Path(audio_path))
        if self._current_audio is None:
            self._play_next()

    @Slot(QMediaPlayer.MediaStatus)
    def _handle_media_status(self, status: QMediaPlayer.MediaStatus) -> None:
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._cleanup_current_audio()
            self._play_next()

    @Slot(QMediaPlayer.Error, str)
    def _handle_player_error(self, _error: QMediaPlayer.Error, error_text: str) -> None:
        self._log_error(f"音频播放失败：{error_text}")
        self._cleanup_current_audio()
        self._play_next()

    @Slot(str)
    def _log_error(self, message: str) -> None:
        print(f"[TTS] {message}")

    def _play_next(self) -> None:
        if not self._pending_audio:
            return
        self._current_audio = self._pending_audio.pop(0)
        self._player.setSource(QUrl.fromLocalFile(str(self._current_audio)))
        self._player.play()

    def _cleanup_current_audio(self) -> None:
        if self._current_audio is None:
            return
        try:
            self._current_audio.unlink(missing_ok=True)
        except OSError as exc:
            self._log_error(f"临时音频清理失败：{exc}")
        self._current_audio = None


def _load_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _get_env_value(values: dict[str, str], key: str, default: str) -> str:
    return os.getenv(key) or values.get(key) or default


def _is_enabled(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _resolve_path(path_text: str, base_dir: Path) -> Path:
    path = Path(path_text.strip().strip('"').strip("'"))
    if path.is_absolute():
        return path
    return base_dir / path


def _load_tone_references(ref_path: Path, base_dir: Path) -> dict[str, list[ToneReference]]:
    if not ref_path.exists():
        return {}

    tone_references: dict[str, list[ToneReference]] = {}
    for raw_line in ref_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        parts = line.split("|", 4)
        if len(parts) != 5:
            continue

        audio_text, _source, lang, prompt_text, tone = [part.strip() for part in parts]
        audio_path = _resolve_path(audio_text, base_dir)
        copied_path = base_dir / "ref" / "tone_refs" / audio_path.name
        if copied_path.exists():
            audio_path = copied_path

        tone_key = tone or DEFAULT_TONE
        reference = ToneReference(
            tone=tone_key,
            ref_audio_path=audio_path,
            ref_text=prompt_text,
            ref_lang=_normalize_lang(lang),
        )
        tone_references.setdefault(tone_key, []).append(reference)

    return tone_references


def _normalize_lang(lang: str) -> str:
    normalized = lang.strip().lower()
    if normalized == "ja":
        return "ja"
    return normalized or "ja"
