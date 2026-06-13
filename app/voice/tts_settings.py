"""app/voice/tts_settings.py — TTS 配置数据模型与 provider 常量。

从 tts.py 拆出的纯配置层：设置数据类、provider/后端常量、语气参考
解析。不依赖 Qt 与网络，可独立测试；新代码统一从本模块导入配置类型与常量。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.config.character_loader import CharacterProfile
from app.llm.chat_reply import DEFAULT_TONE

TTS_PLAYBACK_BACKEND_AUDIO_SINK = "audio_sink"
TTS_PLAYBACK_BACKEND_MEDIA_PLAYER = "media_player"

TTS_PROVIDER_NONE = "none"
TTS_PROVIDER_GPT_SOVITS = "gpt-sovits"
TTS_PROVIDER_CUSTOM_GPT_SOVITS = "custom-gpt-sovits"
TTS_PROVIDER_GENIE = "genie-tts"
DEFAULT_GPT_SOVITS_API_URL = "http://127.0.0.1:9880/tts"
DEFAULT_GENIE_TTS_API_URL = "http://127.0.0.1:9881/"
_SUPPORTED_TTS_PROVIDERS = {
    TTS_PROVIDER_GPT_SOVITS,
    TTS_PROVIDER_CUSTOM_GPT_SOVITS,
    TTS_PROVIDER_GENIE,
}


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
    ref_text_path: Path
    ref_text: str
    provider: str = TTS_PROVIDER_GPT_SOVITS
    gpt_model_path: Path | None = None
    sovits_model_path: Path | None = None
    work_dir: Path | None = None
    python_path: Path | None = None
    tts_config_path: Path | None = None
    character_name: str = ""
    onnx_model_dir: Path | None = None
    ref_lang: str = "ja"
    text_lang: str = "ja"
    timeout_seconds: int = 60
    tone_references: dict[str, list[ToneReference]] = field(default_factory=dict)
    playback_backend: str = ""

    @classmethod
    def from_character_profile(
        cls,
        character_profile: CharacterProfile,
        enabled: bool,
        api_url: str,
        ref_lang: str,
        text_lang: str,
        timeout_seconds: int,
        provider: str = TTS_PROVIDER_GPT_SOVITS,
        work_dir: Path | None = None,
        python_path: Path | None = None,
        tts_config_path: Path | None = None,
        onnx_model_dir: Path | None = None,
        validate_enabled: bool = True,
    ) -> "GPTSoVITSTTSSettings":
        provider = _normalize_tts_provider(provider, enabled)
        if character_profile.voice is None:
            settings = cls(
                provider=provider,
                enabled=enabled,
                api_url=api_url,
                ref_audio_path=character_profile.package_dir,
                ref_text_path=character_profile.package_dir,
                ref_text="",
                ref_lang=ref_lang,
                text_lang=text_lang,
                timeout_seconds=timeout_seconds,
                work_dir=work_dir,
                python_path=python_path,
                tts_config_path=tts_config_path,
                character_name=character_profile.display_name or character_profile.id,
                onnx_model_dir=onnx_model_dir,
            )
            if enabled and validate_enabled:
                settings.validate()
            return settings

        voice = character_profile.voice
        tone_references = _load_tone_references(
            voice.tone_ref_path,
            character_profile.package_dir,
        )
        neutral_reference = _select_neutral_reference(tone_references)
        settings = cls(
            provider=provider,
            enabled=enabled,
            api_url=api_url,
            ref_audio_path=neutral_reference.ref_audio_path if neutral_reference else character_profile.package_dir,
            ref_text_path=neutral_reference.ref_audio_path if neutral_reference else character_profile.package_dir,
            ref_text=neutral_reference.ref_text if neutral_reference else "",
            gpt_model_path=voice.gpt_model_path,
            sovits_model_path=voice.sovits_model_path,
            work_dir=work_dir,
            python_path=python_path,
            tts_config_path=tts_config_path,
            character_name=character_profile.display_name or character_profile.id,
            onnx_model_dir=onnx_model_dir,
            ref_lang=ref_lang,
            text_lang=text_lang,
            timeout_seconds=timeout_seconds,
            tone_references=tone_references,
        )
        if enabled and validate_enabled:
            settings.validate()
        return settings

    def validate(self) -> None:
        if not self.api_url:
            raise TTSConfigError("缺少 TTS API URL。")
        if self.provider not in _SUPPORTED_TTS_PROVIDERS:
            raise TTSConfigError(f"不支持的 TTS Provider：{self.provider}")
        if self.python_path is not None and not self.python_path.exists():
            raise TTSConfigError(f"TTS Python 不存在：{self.python_path}")
        if self.tts_config_path is not None and not self.tts_config_path.exists():
            raise TTSConfigError(f"GPT-SoVITS 推理配置不存在：{self.tts_config_path}")
        if self.gpt_model_path is not None and not self.gpt_model_path.exists():
            raise TTSConfigError(f"GPT 模型不存在：{self.gpt_model_path}")
        if self.sovits_model_path is not None and not self.sovits_model_path.exists():
            raise TTSConfigError(f"SoVITS 模型不存在：{self.sovits_model_path}")
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


def _resolve_path(path_text: str, base_dir: Path) -> Path:
    path = Path(path_text.strip().strip('"').strip("'"))
    if path.is_absolute():
        return path
    return base_dir / path


def _normalize_tts_provider(provider: str, enabled: bool = True) -> str:
    if not enabled:
        return TTS_PROVIDER_NONE
    normalized = provider.strip().lower().replace("_", "-")
    if normalized in {"", "gptsovits"}:
        return TTS_PROVIDER_GPT_SOVITS
    if normalized in {"gpt-so-vits", "gpt-sovits"}:
        return TTS_PROVIDER_GPT_SOVITS
    if normalized in {"custom-gpt-sovits", "external-gpt-sovits", "custom-sovits", "external-sovits"}:
        return TTS_PROVIDER_CUSTOM_GPT_SOVITS
    if normalized in {"genie", "genie-tts", "genietts"}:
        return TTS_PROVIDER_GENIE
    if normalized in {"none", "off", "disabled", "不使用"}:
        return TTS_PROVIDER_NONE
    return normalized


def _load_tone_references(ref_path: Path | None, base_dir: Path) -> dict[str, list[ToneReference]]:
    if ref_path is None or not ref_path.exists():
        return {}

    tone_references: dict[str, list[ToneReference]] = {}
    for raw_line in ref_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        parts = [part.strip() for part in line.split("|")]
        if len(parts) != 4:
            continue

        audio_text, lang, prompt_text, tone = parts
        audio_path = _resolve_path(audio_text, base_dir)
        copied_path = ref_path.parent / "tone_refs" / audio_path.name
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


def _select_neutral_reference(
    tone_references: dict[str, list[ToneReference]],
) -> ToneReference | None:
    neutral_references = tone_references.get(DEFAULT_TONE)
    if neutral_references:
        return neutral_references[0]
    for references in tone_references.values():
        if references:
            return references[0]
    return None


def _normalize_lang(lang: str) -> str:
    normalized = lang.strip().lower()
    if normalized == "ja":
        return "ja"
    return normalized or "ja"
