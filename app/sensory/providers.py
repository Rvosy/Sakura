from __future__ import annotations

import base64
import json
import mimetypes
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any
from typing import Protocol
from urllib.parse import urlparse

from app.sensory.llama_cpp_runtime import (
    DEFAULT_LLAMA_CPP_ALIAS,
    DEFAULT_LLAMA_CPP_HOST,
    DEFAULT_LLAMA_CPP_MANAGED_PORT,
    LLAMA_CPP_MANAGED_RUNTIME_MARKER,
    LlamaCppLaunchConfig,
    LlamaCppRuntimeError,
    LlamaCppRuntimeManager,
)
from app.sensory.models import (
    SensoryObservation,
    SensoryProviderMode,
    SensoryRequest,
    SensorySource,
    generate_sensory_id,
    now_iso,
)
from app.sensory.settings import SensoryProviderConfig


DEFAULT_LMSTUDIO_ENDPOINT = "http://127.0.0.1:1234/v1"
DEFAULT_LLAMA_CPP_ENDPOINT = "http://127.0.0.1:8080/v1"
DEFAULT_OLLAMA_ENDPOINT = "http://127.0.0.1:11434"
DEFAULT_SENSORY_TEMPERATURE = 0.2
_AUDIO_SOURCES = {SensorySource.SPEECH, SensorySource.SOUND}


class SensoryProviderUnavailable(RuntimeError):
    """Raised when a sensory provider cannot safely process a request."""


class SensoryProvider(Protocol):
    provider_id: str
    source: SensorySource
    mode: SensoryProviderMode

    def observe(self, request: SensoryRequest) -> SensoryObservation:
        """Return one structured observation for ``request``."""


class DisabledProvider:
    """Fail-closed provider used for off/missing configuration paths."""

    provider_id = "disabled"
    source = SensorySource.VISION
    mode = SensoryProviderMode.OFF

    def observe(self, request: SensoryRequest) -> SensoryObservation:
        raise SensoryProviderUnavailable(f"Sensory source is disabled: {request.source.value}")


class ApiSensoryProvider:
    """OpenAI-compatible API sensory provider."""

    def __init__(self, config: SensoryProviderConfig) -> None:
        self.config = config.normalized()
        self.provider_id = self.config.provider_id
        self.source = self.config.source
        self.mode = SensoryProviderMode.API
        self._transport = _OpenAICompatibleTransport(self.config)

    def observe(self, request: SensoryRequest) -> SensoryObservation:
        return self._transport.observe(request)


class LocalSensoryProvider:
    """Generic OpenAI-compatible local sensory provider."""

    def __init__(self, config: SensoryProviderConfig) -> None:
        self.config = config.normalized()
        self.provider_id = self.config.provider_id
        self.source = self.config.source
        self.mode = SensoryProviderMode.LOCAL
        self._transport = _OpenAICompatibleTransport(self.config)

    def observe(self, request: SensoryRequest) -> SensoryObservation:
        return self._transport.observe(request)


class LmStudioSensoryProvider(LocalSensoryProvider):
    """LM Studio local server adapter using its OpenAI-compatible API."""

    def __init__(self, config: SensoryProviderConfig) -> None:
        super().__init__(_with_default_endpoint(config, DEFAULT_LMSTUDIO_ENDPOINT))


class LlamaCppSensoryProvider(LocalSensoryProvider):
    """llama.cpp llama-server adapter using its OpenAI-compatible API."""

    def __init__(self, config: SensoryProviderConfig) -> None:
        super().__init__(_with_default_endpoint(config, DEFAULT_LLAMA_CPP_ENDPOINT))


class ManagedLlamaCppSensoryProvider:
    """llama.cpp provider that starts a Sakura-managed ``llama-server`` on demand."""

    def __init__(
        self,
        config: SensoryProviderConfig,
        *,
        base_dir: Path,
        resource_registry: Any | None = None,
    ) -> None:
        self.config = _with_default_endpoint(config, _managed_llama_cpp_endpoint(config)).normalized()
        self.provider_id = self.config.provider_id
        self.source = self.config.source
        self.mode = SensoryProviderMode.LOCAL
        self._runtime_manager = LlamaCppRuntimeManager(
            base_dir=base_dir,
            resource_registry=resource_registry,
        )
        self._provider = LlamaCppSensoryProvider(
            _with_model(self.config, _llama_cpp_model_alias(self.config.model))
        )
        self._runtime_ready = False

    def observe(self, request: SensoryRequest) -> SensoryObservation:
        self._ensure_runtime()
        return self._provider.observe(request)

    def _ensure_runtime(self) -> None:
        if self._runtime_ready:
            return
        try:
            status = self._runtime_manager.start(_llama_cpp_launch_config_from_provider(self.config))
        except LlamaCppRuntimeError as exc:
            raise SensoryProviderUnavailable(str(exc)) from exc
        if not status.healthy:
            raise SensoryProviderUnavailable("llama.cpp sensory runtime is not healthy")
        self._runtime_ready = status.healthy


class OllamaSensoryProvider:
    """Ollama native /api/chat adapter."""

    def __init__(self, config: SensoryProviderConfig) -> None:
        self.config = _with_default_endpoint(config, DEFAULT_OLLAMA_ENDPOINT).normalized()
        self.provider_id = self.config.provider_id
        self.source = self.config.source
        self.mode = SensoryProviderMode.LOCAL

    def observe(self, request: SensoryRequest) -> SensoryObservation:
        normalized_request = request.normalized()
        model = self.config.model.strip()
        if not model:
            raise SensoryProviderUnavailable(f"Ollama sensory provider {self.provider_id} has no model")
        if normalized_request.source in _AUDIO_SOURCES:
            if not _request_media_refs(normalized_request):
                raise SensoryProviderUnavailable(
                    f"Ollama sensory provider {self.provider_id} requires an audio media_ref"
                )
            raise SensoryProviderUnavailable(
                f"Ollama sensory provider {self.provider_id} does not support audio input"
            )
        images = _request_image_base64s(normalized_request)
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": _build_sensory_system_prompt(normalized_request.source),
                },
                {
                    "role": "user",
                    "content": _build_sensory_user_prompt(normalized_request),
                    **({"images": images} if images else {}),
                },
            ],
            "stream": False,
            "format": str(self.config.extra.get("format") or "json"),
            "options": {
                "temperature": _float_extra(self.config.extra, "temperature", DEFAULT_SENSORY_TEMPERATURE),
            },
        }
        data = _post_json(
            _ollama_chat_url(self.config.endpoint),
            payload,
            headers=_auth_headers(self.config.api_key),
            timeout_seconds=self.config.timeout_seconds,
        )
        message = data.get("message")
        if not isinstance(message, dict):
            raise SensoryProviderUnavailable(f"Ollama sensory provider {self.provider_id} returned invalid response")
        return _observation_from_model_content(
            str(message.get("content") or ""),
            normalized_request,
            provider_id=self.provider_id,
            mode=self.mode,
        )


class FakeSensoryProvider:
    """Test provider that returns deterministic structured observations."""

    def __init__(
        self,
        provider_id: str = "fake",
        source: SensorySource = SensorySource.VISION,
        mode: SensoryProviderMode = SensoryProviderMode.LOCAL,
        *,
        summary: str = "fake sensory observation",
        confidence: float = 0.9,
        factory: Callable[[SensoryRequest], SensoryObservation] | None = None,
    ) -> None:
        self.provider_id = provider_id
        self.source = source
        self.mode = mode
        self.summary = summary
        self.confidence = confidence
        self.factory = factory

    def observe(self, request: SensoryRequest) -> SensoryObservation:
        normalized_request = request.normalized()
        if self.factory is not None:
            return self.factory(normalized_request).normalized()
        return SensoryObservation(
            id=generate_sensory_id("fake"),
            source=normalized_request.source,
            created_at=now_iso(),
            summary=self.summary,
            details={"request_text": normalized_request.text},
            confidence=self.confidence,
            provider_id=self.provider_id,
            mode=self.mode,
            user_text=normalized_request.user_text,
            event_type=normalized_request.event_type,
            metadata={"request_id": normalized_request.id},
        ).normalized()


def provider_from_config(
    config: SensoryProviderConfig,
    *,
    base_dir: Path | None = None,
    resource_registry: Any | None = None,
) -> SensoryProvider:
    normalized = config.normalized()
    if normalized.mode == SensoryProviderMode.API:
        return ApiSensoryProvider(normalized)
    if normalized.mode == SensoryProviderMode.LOCAL:
        backend = _backend_name(normalized)
        if backend in {"lmstudio", "lm_studio"}:
            return LmStudioSensoryProvider(normalized)
        if backend in {"llama", "llama.cpp", "llama_cpp", "llamacpp"}:
            if base_dir is not None and _managed_llama_cpp_enabled(normalized):
                return ManagedLlamaCppSensoryProvider(
                    normalized,
                    base_dir=base_dir,
                    resource_registry=resource_registry,
                )
            return LlamaCppSensoryProvider(normalized)
        if backend == "ollama":
            return OllamaSensoryProvider(normalized)
        return LocalSensoryProvider(normalized)
    return DisabledProvider()


def build_provider_registry(
    configs: dict[str, SensoryProviderConfig],
    *,
    base_dir: Path | None = None,
    resource_registry: Any | None = None,
) -> dict[str, SensoryProvider]:
    return {
        provider.provider_id: provider
        for provider in (
            provider_from_config(
                config,
                base_dir=base_dir,
                resource_registry=resource_registry,
            )
            for config in configs.values()
        )
    }


class _OpenAICompatibleTransport:
    def __init__(self, config: SensoryProviderConfig) -> None:
        self.config = config.normalized()
        self.provider_id = self.config.provider_id
        self.source = self.config.source
        self.mode = self.config.mode

    def observe(self, request: SensoryRequest) -> SensoryObservation:
        normalized_request = request.normalized()
        if not self.config.endpoint:
            raise SensoryProviderUnavailable(f"Sensory provider {self.provider_id} has no endpoint")
        if not self.config.model:
            raise SensoryProviderUnavailable(f"Sensory provider {self.provider_id} has no model")
        media_refs = _request_media_refs(normalized_request)
        if normalized_request.source in _AUDIO_SOURCES and not media_refs:
            raise SensoryProviderUnavailable(
                f"Sensory provider {self.provider_id} requires an audio media_ref"
            )
        image_urls = _request_image_urls(normalized_request)
        audio_inputs = _request_audio_inputs(normalized_request)
        if normalized_request.source in _AUDIO_SOURCES and not audio_inputs:
            raise SensoryProviderUnavailable(
                f"Sensory provider {self.provider_id} received no supported audio media"
            )
        content: list[dict[str, Any]] = [
            {"type": "text", "text": _build_sensory_user_prompt(normalized_request)}
        ]
        content.extend(
            {
                "type": "image_url",
                "image_url": {
                    "url": image_url,
                    "detail": str(self.config.extra.get("image_detail") or "low"),
                },
            }
            for image_url in image_urls
        )
        content.extend(
            {"type": "input_audio", "input_audio": audio_input}
            for audio_input in audio_inputs
        )
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": _build_sensory_system_prompt(normalized_request.source)},
                {"role": "user", "content": content},
            ],
            "temperature": _float_extra(self.config.extra, "temperature", DEFAULT_SENSORY_TEMPERATURE),
            "max_tokens": _int_extra(self.config.extra, "max_tokens", 512),
        }
        if bool(self.config.extra.get("response_format")):
            payload["response_format"] = self.config.extra["response_format"]
        data = _post_json(
            _openai_chat_completions_url(self.config.endpoint),
            payload,
            headers=_auth_headers(self.config.api_key),
            timeout_seconds=self.config.timeout_seconds,
        )
        try:
            content_text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise SensoryProviderUnavailable(
                f"Sensory provider {self.provider_id} returned invalid response"
            ) from exc
        return _observation_from_model_content(
            str(content_text or ""),
            normalized_request,
            provider_id=self.provider_id,
            mode=self.mode,
        )


def _post_json(
    url: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str],
    timeout_seconds: int,
) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise SensoryProviderUnavailable(f"HTTP {exc.code}: {body}") from exc
    except (OSError, TimeoutError, json.JSONDecodeError) as exc:
        raise SensoryProviderUnavailable(str(exc)) from exc
    if not isinstance(data, dict):
        raise SensoryProviderUnavailable("provider returned non-object JSON")
    return data


def _build_sensory_system_prompt(source: SensorySource) -> str:
    return (
        "You are Sakura's sensory middleware. Convert the input into compact "
        "structured evidence for a desktop pet. Return JSON only. Do not roleplay. "
        "Never include raw media. Redact secrets, passwords, API keys, tokens, ID "
        "numbers, and payment card numbers as [REDACTED]. "
        f"The sensory source is {source.value}."
    )


def _build_sensory_user_prompt(request: SensoryRequest) -> str:
    data = {
        "source": request.source.value,
        "user_text": request.user_text,
        "event_type": request.event_type,
        "text": request.text,
        "metadata": _safe_prompt_metadata(request.metadata),
        "schema": {
            "summary": "one to three concise sentences",
            "details": {
                "visible_texts": ["clear OCR text, if any"],
                "uncertain_texts": ["uncertain OCR text, if any"],
                "notable_elements": ["objects, UI elements, speakers, or sound events"],
                "labels": ["short tags"],
            },
            "confidence": 0.0,
            "sensitive_redacted": False,
        },
    }
    return json.dumps(data, ensure_ascii=False, indent=2)


def _observation_from_model_content(
    content: str,
    request: SensoryRequest,
    *,
    provider_id: str,
    mode: SensoryProviderMode,
) -> SensoryObservation:
    parsed = _load_json_object(content)
    if parsed is None:
        summary = content.strip()
        details: dict[str, Any] = {}
        confidence = 0.5 if summary else 0.0
        sensitive_redacted = False
    else:
        summary = str(parsed.get("summary") or "").strip()
        details = parsed.get("details") if isinstance(parsed.get("details"), dict) else {}
        for legacy_key in ("visible_texts", "uncertain_texts", "notable_elements", "labels"):
            if legacy_key in parsed and legacy_key not in details:
                details[legacy_key] = parsed[legacy_key]
        confidence = _confidence_value(parsed.get("confidence"))
        sensitive_redacted = bool(parsed.get("sensitive_redacted", False))
    return SensoryObservation(
        id=generate_sensory_id("sensory"),
        source=request.source,
        created_at=now_iso(),
        summary=summary or "感官模型未返回摘要。",
        details=details,
        confidence=confidence,
        provider_id=provider_id,
        mode=mode,
        user_text=request.user_text,
        event_type=request.event_type,
        sensitive_redacted=sensitive_redacted,
        metadata={"request_id": request.id},
    ).normalized()


def _load_json_object(content: str) -> dict[str, Any] | None:
    text = content.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None


def _request_image_urls(request: SensoryRequest) -> list[str]:
    if request.source != SensorySource.VISION:
        return []
    refs = _request_media_refs(request)
    urls: list[str] = []
    for ref in refs:
        url = _media_ref_to_image_url(ref)
        if url:
            urls.append(url)
    return urls[:4]


def _request_image_base64s(request: SensoryRequest) -> list[str]:
    if request.source != SensorySource.VISION:
        return []
    images: list[str] = []
    for ref in _request_media_refs(request):
        image = _media_ref_to_base64(ref)
        if image:
            images.append(image)
    return images[:4]


def _request_audio_inputs(request: SensoryRequest) -> list[dict[str, str]]:
    if request.source not in _AUDIO_SOURCES:
        return []
    audio_inputs: list[dict[str, str]] = []
    for ref in _request_media_refs(request):
        audio_input = _media_ref_to_input_audio(ref)
        if audio_input:
            audio_inputs.append(audio_input)
    return audio_inputs[:2]


def _request_media_refs(request: SensoryRequest) -> list[str]:
    refs: list[str] = []
    _append_text_ref(refs, request.media_ref)
    metadata = request.metadata
    for key in ("data_url", "image_url", "audio_url", "media_ref", "path"):
        _append_text_ref(refs, metadata.get(key))
    for key in ("image_urls", "audio_urls", "images", "audios", "media_refs"):
        value = metadata.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    _append_text_ref(refs, item.get("url") or item.get("data_url") or item.get("path"))
                else:
                    _append_text_ref(refs, item)
    return refs


def _append_text_ref(refs: list[str], value: Any) -> None:
    text = str(value or "").strip()
    if text:
        refs.append(text)


def _media_ref_to_image_url(ref: str) -> str:
    if ref.startswith("data:image/") or ref.startswith("http://") or ref.startswith("https://"):
        return ref
    path = Path(ref).expanduser()
    if path.is_file():
        mime = mimetypes.guess_type(path.name)[0]
        if mime and not mime.startswith("image/"):
            return ""
        mime = mime or "image/png"
        return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"
    return ""


def _media_ref_to_base64(ref: str) -> str:
    if ref.startswith("data:image/"):
        _prefix, _separator, payload = ref.partition(",")
        return payload.strip()
    path = Path(ref).expanduser()
    if path.is_file():
        mime = mimetypes.guess_type(path.name)[0]
        if mime and not mime.startswith("image/"):
            return ""
        return base64.b64encode(path.read_bytes()).decode("ascii")
    return ""


def _media_ref_to_input_audio(ref: str) -> dict[str, str] | None:
    if ref.startswith("data:audio/"):
        header, separator, payload = ref.partition(",")
        if not separator or not payload.strip():
            return None
        audio_format = _audio_format_from_mime(header.removeprefix("data:").split(";", 1)[0])
        return {"data": payload.strip(), "format": audio_format}
    path = Path(ref).expanduser()
    if path.is_file():
        mime = mimetypes.guess_type(path.name)[0]
        if mime and not mime.startswith("audio/"):
            return None
        audio_format = _audio_format_from_mime(mime or "", path)
        return {
            "data": base64.b64encode(path.read_bytes()).decode("ascii"),
            "format": audio_format,
        }
    return None


def _audio_format_from_mime(mime: str, path: Path | None = None) -> str:
    normalized = mime.strip().lower()
    if normalized in {"audio/wav", "audio/x-wav", "audio/wave"}:
        return "wav"
    if normalized in {"audio/mpeg", "audio/mp3"}:
        return "mp3"
    if normalized in {"audio/mp4", "audio/x-m4a"}:
        return "m4a"
    if normalized == "audio/ogg":
        return "ogg"
    if normalized == "audio/flac":
        return "flac"
    if path is not None and path.suffix:
        return path.suffix.lstrip(".").lower()
    if normalized.startswith("audio/"):
        return normalized.split("/", 1)[1].split(";", 1)[0] or "wav"
    return "wav"


def _safe_prompt_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    hidden_keys = {
        "audio",
        "audio_bytes",
        "data_url",
        "image",
        "image_bytes",
        "image_url",
        "image_urls",
        "images",
        "media",
        "media_bytes",
        "media_ref",
        "media_refs",
        "raw_audio",
        "raw_image",
        "raw_media",
    }
    return {str(key): value for key, value in metadata.items() if str(key) not in hidden_keys}


def _openai_chat_completions_url(endpoint: str) -> str:
    base = endpoint.strip().rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def _ollama_chat_url(endpoint: str) -> str:
    base = endpoint.strip().rstrip("/")
    if base.endswith("/api/chat"):
        return base
    return f"{base}/api/chat"


def _auth_headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


def _with_default_endpoint(config: SensoryProviderConfig, endpoint: str) -> SensoryProviderConfig:
    normalized = config.normalized()
    if normalized.endpoint:
        return normalized
    return SensoryProviderConfig(
        provider_id=normalized.provider_id,
        source=normalized.source,
        mode=normalized.mode,
        endpoint=endpoint,
        model=normalized.model,
        api_key=normalized.api_key,
        timeout_seconds=normalized.timeout_seconds,
        extra=normalized.extra,
    )


def _with_model(config: SensoryProviderConfig, model: str) -> SensoryProviderConfig:
    normalized = config.normalized()
    return SensoryProviderConfig(
        provider_id=normalized.provider_id,
        source=normalized.source,
        mode=normalized.mode,
        endpoint=normalized.endpoint,
        model=model or normalized.model,
        api_key=normalized.api_key,
        timeout_seconds=normalized.timeout_seconds,
        extra=normalized.extra,
    ).normalized()


def _managed_llama_cpp_enabled(config: SensoryProviderConfig) -> bool:
    extra = config.extra
    marker = str(extra.get("managed_runtime") or "").strip().lower()
    return (
        marker == LLAMA_CPP_MANAGED_RUNTIME_MARKER
        or bool(str(extra.get("llama_binary_path") or "").strip())
        or bool(str(extra.get("llama_runtime_package_id") or "").strip())
    )


def _managed_llama_cpp_endpoint(config: SensoryProviderConfig) -> str:
    if config.endpoint:
        return config.endpoint
    return f"http://{DEFAULT_LLAMA_CPP_HOST}:{DEFAULT_LLAMA_CPP_MANAGED_PORT}/v1"


def _llama_cpp_launch_config_from_provider(config: SensoryProviderConfig) -> LlamaCppLaunchConfig:
    endpoint = _managed_llama_cpp_endpoint(config)
    host, port = _host_port_from_endpoint(endpoint)
    model = config.model.strip()
    model_path, hf_repo, mmproj_path = _llama_cpp_model_paths(model, config.extra)
    return LlamaCppLaunchConfig(
        binary_path=str(config.extra.get("llama_binary_path") or "").strip(),
        model_path=model_path,
        hf_repo=hf_repo,
        mmproj_path=mmproj_path,
        host=host,
        port=port,
        alias=_llama_cpp_model_alias(model),
        ctx_size=_int_extra(config.extra, "ctx_size", 4096),
        n_gpu_layers=config.extra.get("n_gpu_layers", "auto"),
        threads=_int_extra(config.extra, "threads", 0),
        timeout_seconds=float(config.timeout_seconds),
        extra_args=_llama_cpp_extra_args(config.extra),
    )


def _host_port_from_endpoint(endpoint: str) -> tuple[str, int]:
    parsed = urlparse(endpoint.strip())
    host = parsed.hostname or DEFAULT_LLAMA_CPP_HOST
    port = parsed.port or DEFAULT_LLAMA_CPP_MANAGED_PORT
    return host, port


def _llama_cpp_model_paths(model: str, extra: dict[str, Any]) -> tuple[str, str, str]:
    explicit_model_path = str(extra.get("model_path") or "").strip()
    explicit_hf_repo = str(extra.get("hf_repo") or "").strip()
    explicit_mmproj = str(extra.get("mmproj_path") or "").strip()
    if explicit_model_path or explicit_hf_repo:
        return explicit_model_path, explicit_hf_repo, explicit_mmproj
    model_text = model.strip()
    if not model_text:
        return "", "", explicit_mmproj
    path = Path(model_text).expanduser()
    if path.is_file():
        return str(path), "", explicit_mmproj
    if path.is_dir():
        model_path, mmproj_path = _find_llama_cpp_model_files(path)
        return str(model_path) if model_path is not None else "", "", explicit_mmproj or (
            str(mmproj_path) if mmproj_path is not None else ""
        )
    if "/" in model_text:
        return "", model_text, explicit_mmproj
    return model_text, "", explicit_mmproj


def _find_llama_cpp_model_files(directory: Path) -> tuple[Path | None, Path | None]:
    try:
        ggufs = sorted(directory.rglob("*.gguf"))
    except OSError:
        return None, None
    mmproj = next((path for path in ggufs if "mmproj" in path.name.lower()), None)
    model = next((path for path in ggufs if path != mmproj), None)
    return model, mmproj


def _llama_cpp_model_alias(model: str) -> str:
    text = model.strip()
    if not text:
        return DEFAULT_LLAMA_CPP_ALIAS
    if "/" in text and not Path(text).expanduser().exists():
        return text
    path = Path(text).expanduser()
    if path.is_dir():
        return path.name or DEFAULT_LLAMA_CPP_ALIAS
    if path.exists() and path.suffix:
        return path.stem or DEFAULT_LLAMA_CPP_ALIAS
    if text.lower().endswith(".gguf"):
        return path.stem or DEFAULT_LLAMA_CPP_ALIAS
    return text


def _llama_cpp_extra_args(extra: dict[str, Any]) -> tuple[str, ...]:
    raw = extra.get("llama_extra_args")
    if isinstance(raw, list):
        return tuple(str(item).strip() for item in raw if str(item).strip())
    if isinstance(raw, str) and raw.strip():
        return tuple(part for part in raw.strip().split(" ") if part)
    return ()


def _backend_name(config: SensoryProviderConfig) -> str:
    explicit = config.extra.get("backend") or config.extra.get("provider")
    if explicit:
        return str(explicit).strip().lower()
    text = " ".join([config.provider_id, config.endpoint]).lower()
    if "lmstudio" in text or "lm-studio" in text or "127.0.0.1:1234" in text:
        return "lmstudio"
    if "ollama" in text or "127.0.0.1:11434" in text:
        return "ollama"
    if "llama" in text or "127.0.0.1:8080" in text:
        return "llama"
    return ""


def _float_extra(extra: dict[str, Any], key: str, default: float) -> float:
    try:
        return float(extra.get(key, default))
    except (TypeError, ValueError):
        return default


def _int_extra(extra: dict[str, Any], key: str, default: int) -> int:
    try:
        return int(extra.get(key, default))
    except (TypeError, ValueError):
        return default


def _confidence_value(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    return max(0.0, min(1.0, number))
