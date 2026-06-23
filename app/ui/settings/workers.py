"""app/ui/settings/workers.py — 设置窗口的后台 Worker。

从 settings_dialog.py 拆出：API 连通性测试、模型列表探测、TTS 试听、
记忆列表加载、嵌入模型导入、主题 AI 生成、角色包导出。
全部为纯 QObject worker，不持有任何设置页控件。
"""

from __future__ import annotations

import json
import mimetypes
import shutil
import subprocess
import urllib.error
import urllib.request
import base64
from pathlib import Path
from typing import Callable, Literal

from PySide6.QtCore import QObject, Signal, Slot

from app.agent.memory import MemoryStore
from app.backchannel.model_cache import (
    download_backchannel_model,
    import_backchannel_model_archive,
)
from app.config.character_archive import (
    CharacterArchiveError,
    export_character_archive,
    export_character_voice_archive,
)
from app.config.character_loader import CharacterProfile
from app.core.debug_log import debug_log
from app.llm.api_client import ApiSettings, OpenAICompatibleClient
from app.llm.prompts.recipes import build_theme_color_system_prompt
from app.sensory.audio_smoke import (
    build_sensory_audio_smoke_data_url,
    run_sensory_audio_smoke_test,
)
from app.sensory.audio_runtime_doctor import build_sensory_audio_runtime_doctor_report
from app.sensory.audio_models import (
    llama_cpp_audio_cache_ready,
    llama_cpp_audio_model_repo_id,
    recommended_llama_cpp_audio_model,
)
from app.sensory.models import SensoryRequest, SensorySource
from app.sensory.llama_cpp_runtime import (
    LlamaCppRuntimeError,
    discover_llama_server_binary,
    fetch_llama_cpp_runtime_package_catalog,
    install_llama_cpp_runtime_package,
    llama_cpp_platform_key,
    select_llama_cpp_runtime_package,
)
from app.sensory.providers import (
    DEFAULT_LLAMA_CPP_ENDPOINT,
    DEFAULT_LMSTUDIO_ENDPOINT,
    DEFAULT_OLLAMA_ENDPOINT,
    provider_from_config,
)
from app.sensory.settings import SensoryProviderConfig
from app.storage.paths import StoragePaths
from app.ui.theme import parse_ai_theme_response
from app.voice.factory import create_tts_provider
from app.voice.tts_settings import GPTSoVITSTTSSettings


_SENSORY_TEST_IMAGE_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAIAAACQkWg2AAAACXBIWXMAAA9hAAAPYQGoP6dp"
    "AAAAGUlEQVQokWO84+DAQApgIkn1qIZRDUNKAwBb8AF8KOWdWAAAAABJRU5ErkJggg=="
)
_SENSORY_TEST_AUDIO_DATA_URL = build_sensory_audio_smoke_data_url()
HF_CLI_INSTALL_HINT = (
    "未找到 Hugging Face CLI `hf`。请先安装："
    "macOS/Linux 运行 `curl -LsSf https://hf.co/cli/install.sh | bash`；"
    "Windows 运行 `powershell -ExecutionPolicy ByPass -c \"irm https://hf.co/cli/install.ps1 | iex\"`。"
)
HF_MODEL_SEARCH_LIMIT = 20
HF_MODEL_DOWNLOAD_TIMEOUT_SECONDS = 60 * 60
HF_COMPATIBILITY_CLEAR = "clear"
HF_COMPATIBILITY_POSSIBLE = "possible"
HF_COMPATIBILITY_UNKNOWN = "unknown"


def _image_file_to_data_url(path: Path) -> str:
    data = path.read_bytes()
    mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
    if not mime_type.startswith("image/"):
        mime_type = "image/png"
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


class ApiConnectionTestWorker(QObject):
    succeeded = Signal(str)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, settings: ApiSettings) -> None:
        super().__init__()
        self.settings = settings

    @Slot()
    def run(self) -> None:
        try:
            message = OpenAICompatibleClient(self.settings).test_connection()
        except Exception as exc:  # UI 边界统一转成可读错误。
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit(message)
        finally:
            self.finished.emit()


class ApiModelListProbeWorker(QObject):
    succeeded = Signal(list)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, settings: ApiSettings) -> None:
        super().__init__()
        self.settings = settings

    @Slot()
    def run(self) -> None:
        try:
            models = OpenAICompatibleClient(self.settings).list_models()
        except Exception as exc:  # UI 边界统一转成可读错误。
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit(models)
        finally:
            self.finished.emit()


class SensoryModelListProbeWorker(QObject):
    succeeded = Signal(list)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, config: SensoryProviderConfig) -> None:
        super().__init__()
        self.config = config.normalized()

    @Slot()
    def run(self) -> None:
        try:
            models = _probe_sensory_models(self.config)
        except Exception as exc:  # UI 边界统一转成可读错误。
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit(models)
        finally:
            self.finished.emit()


class SensoryModelTestWorker(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(
        self,
        config: SensoryProviderConfig,
        source: SensorySource,
        *,
        base_dir: Path | None = None,
    ) -> None:
        super().__init__()
        self.config = config.normalized()
        self.source = source
        self.base_dir = base_dir

    @Slot()
    def run(self) -> None:
        try:
            if self.source in {SensorySource.SPEECH, SensorySource.SOUND}:
                result = run_sensory_audio_smoke_test(
                    self.config,
                    base_dir=self.base_dir,
                    source=self.source,
                )
                if not result.ok:
                    raise RuntimeError(result.message)
                observation = result.observation
                if observation is None:
                    raise RuntimeError("音频推理 smoke test 未返回观察结果。")
                self.succeeded.emit(observation.to_dict())
                return
            provider = provider_from_config(self.config, base_dir=self.base_dir)
            request = SensoryRequest(
                id="settings_test",
                source=self.source,
                user_text="设置页测试增强感知模型",
                event_type="settings_test",
                text=_sensory_test_text(self.source),
                media_ref=_sensory_test_media_ref(self.source),
                metadata={"test": True},
            )
            observation = provider.observe(request)
        except Exception as exc:  # UI 边界统一转成可读错误。
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit(observation.to_dict())
        finally:
            self.finished.emit()


class HuggingFaceModelSearchWorker(QObject):
    succeeded = Signal(list)
    failed = Signal(str)
    finished = Signal()

    def __init__(
        self,
        source: SensorySource,
        query: str,
        *,
        limit: int = HF_MODEL_SEARCH_LIMIT,
        timeout_seconds: int = 60,
    ) -> None:
        super().__init__()
        self.source = source
        self.query = query
        self.limit = limit
        self.timeout_seconds = timeout_seconds

    @Slot()
    def run(self) -> None:
        try:
            models = search_huggingface_models(
                self.source,
                self.query,
                limit=self.limit,
                timeout_seconds=self.timeout_seconds,
            )
        except Exception as exc:  # UI 边界统一转成可读错误。
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit(models)
        finally:
            self.finished.emit()


class HuggingFaceModelDownloadWorker(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(
        self,
        base_dir: Path,
        source: SensorySource,
        repo_id: str,
        *,
        timeout_seconds: int = HF_MODEL_DOWNLOAD_TIMEOUT_SECONDS,
    ) -> None:
        super().__init__()
        self.base_dir = base_dir
        self.source = source
        self.repo_id = repo_id
        self.timeout_seconds = timeout_seconds

    @Slot()
    def run(self) -> None:
        try:
            local_dir = StoragePaths(self.base_dir).sensory_model_cache_for(
                self.source.value,
                self.repo_id,
            )
            result = download_huggingface_model(
                self.repo_id,
                local_dir,
                timeout_seconds=self.timeout_seconds,
            )
            result["source"] = self.source.value
        except Exception as exc:  # UI 边界统一转成可读错误。
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit(result)
        finally:
            self.finished.emit()


class LlamaCppRuntimeInstallWorker(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(
        self,
        base_dir: Path,
        *,
        timeout_seconds: int = HF_MODEL_DOWNLOAD_TIMEOUT_SECONDS,
    ) -> None:
        super().__init__()
        self.base_dir = base_dir
        self.timeout_seconds = timeout_seconds

    @Slot()
    def run(self) -> None:
        try:
            payload = _ensure_llama_cpp_runtime(
                self.base_dir,
                timeout_seconds=self.timeout_seconds,
            )
        except (LlamaCppRuntimeError, RuntimeError, OSError) as exc:
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit(payload)
        finally:
            self.finished.emit()


class LlamaCppAudioBackendPrepareWorker(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(
        self,
        base_dir: Path,
        source: SensorySource,
        *,
        timeout_seconds: int = HF_MODEL_DOWNLOAD_TIMEOUT_SECONDS,
    ) -> None:
        super().__init__()
        self.base_dir = base_dir
        self.source = source
        self.timeout_seconds = timeout_seconds

    @Slot()
    def run(self) -> None:
        try:
            payload = prepare_llama_cpp_audio_backend(
                self.base_dir,
                self.source,
                download_model=True,
                timeout_seconds=self.timeout_seconds,
            )
        except (LlamaCppRuntimeError, RuntimeError, OSError) as exc:
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit(payload)
        finally:
            self.finished.emit()


class LlamaCppRuntimeDoctorWorker(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, base_dir: Path) -> None:
        super().__init__()
        self.base_dir = base_dir

    @Slot()
    def run(self) -> None:
        try:
            report = build_sensory_audio_runtime_doctor_report(self.base_dir)
        except Exception as exc:  # UI 边界统一转成可读错误。
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit(report)
        finally:
            self.finished.emit()


class TTSTestWorker(QObject):
    succeeded = Signal(object, str)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, settings: GPTSoVITSTTSSettings, *, base_dir: Path | None = None) -> None:
        super().__init__()
        self.settings = settings
        self.base_dir = base_dir

    @Slot()
    def run(self) -> None:
        provider = None
        should_close_provider = True
        try:
            provider = create_tts_provider(
                self.settings,
                base_dir=self.base_dir,
                adopt_existing_service=False,
            )
            ok, message = provider.ensure_ready()
            if ok:
                should_close_provider = False
                self.succeeded.emit(provider.settings, message)
            else:
                self.failed.emit(message)
        except Exception as exc:  # UI 边界统一转成可读错误。
            self.failed.emit(str(exc))
        finally:
            if should_close_provider and provider is not None:
                close = getattr(provider, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception as exc:  # noqa: BLE001
                        debug_log("TTS", "TTS 检测失败后清理 Provider 失败", {"error": str(exc)})
            self.finished.emit()


def _probe_sensory_models(config: SensoryProviderConfig) -> list[str]:
    backend = str(config.extra.get("backend") or config.extra.get("provider") or "").strip().lower()
    if backend == "ollama":
        data = _get_json(
            _ollama_tags_url(config.endpoint),
            config.timeout_seconds,
            api_key=config.api_key,
        )
        models = data.get("models")
        if not isinstance(models, list):
            return []
        names: list[str] = []
        for item in models:
            if isinstance(item, dict):
                name = item.get("name") or item.get("model")
                if isinstance(name, str) and name.strip():
                    names.append(name.strip())
        return names
    data = _get_json(
        _openai_models_url(_default_sensory_endpoint(config)),
        config.timeout_seconds,
        api_key=config.api_key,
    )
    raw_models = data.get("data")
    if not isinstance(raw_models, list):
        return []
    names = []
    for item in raw_models:
        if isinstance(item, dict):
            model_id = item.get("id")
            if isinstance(model_id, str) and model_id.strip():
                names.append(model_id.strip())
    return names


def default_huggingface_query_for_source(source: SensorySource) -> str:
    if source == SensorySource.SPEECH:
        return "automatic-speech-recognition whisper qwen audio"
    if source == SensorySource.SOUND:
        return "audio-classification sound event"
    return "vision-language qwen vl instruct"


def primary_huggingface_task_filter_for_source(source: SensorySource) -> str:
    if source == SensorySource.SPEECH:
        return "automatic-speech-recognition"
    if source == SensorySource.SOUND:
        return "audio-classification"
    return "image-text-to-text"


def search_huggingface_models(
    source: SensorySource,
    query: str,
    *,
    limit: int = HF_MODEL_SEARCH_LIMIT,
    timeout_seconds: int = 60,
) -> list[dict[str, object]]:
    text = query.strip() or default_huggingface_query_for_source(source)
    count = max(1, min(int(limit), 50))
    strict_models = _run_huggingface_model_search(
        text,
        count,
        timeout_seconds=timeout_seconds,
        task_filter=primary_huggingface_task_filter_for_source(source),
    )
    strict_marked = _mark_huggingface_model_compatibility(source, strict_models)
    clear_models = [
        model
        for model in strict_marked
        if model.get("compatibility") == HF_COMPATIBILITY_CLEAR
    ]
    if clear_models:
        return clear_models
    broad_models = _run_huggingface_model_search(
        text,
        count,
        timeout_seconds=timeout_seconds,
        task_filter="",
    )
    return _sort_huggingface_model_results(
        _mark_huggingface_model_compatibility(source, broad_models)
    )


def _run_huggingface_model_search(
    text: str,
    limit: int,
    *,
    timeout_seconds: int,
    task_filter: str = "",
) -> list[dict[str, object]]:
    args = [
        "models",
        "list",
        "--search",
        text,
        "--limit",
        str(limit),
        "--format",
        "json",
    ]
    if task_filter:
        args.extend(["--filter", task_filter])
    completed = _run_hf_command(args, timeout_seconds=timeout_seconds)
    return _parse_huggingface_model_results(completed.stdout)


def download_huggingface_model(
    repo_id: str,
    local_dir: Path,
    *,
    include_patterns: tuple[str, ...] = (),
    timeout_seconds: int = HF_MODEL_DOWNLOAD_TIMEOUT_SECONDS,
) -> dict[str, object]:
    normalized_repo_id = repo_id.strip()
    if not normalized_repo_id or "/" not in normalized_repo_id:
        raise RuntimeError("请选择有效的 Hugging Face 模型仓库 ID。")
    target = Path(local_dir)
    target.mkdir(parents=True, exist_ok=True)
    args = [
        "download",
        normalized_repo_id,
        "--local-dir",
        str(target),
    ]
    for pattern in include_patterns:
        normalized_pattern = str(pattern or "").strip()
        if normalized_pattern:
            args.extend(["--include", normalized_pattern])
    completed = _run_hf_command(args, timeout_seconds=timeout_seconds)
    return {
        "repo_id": normalized_repo_id,
        "local_dir": str(target),
        "include_patterns": list(include_patterns),
        "message": (completed.stdout or completed.stderr or "").strip(),
    }


def prepare_llama_cpp_audio_backend(
    base_dir: Path,
    source: SensorySource,
    *,
    download_model: bool,
    timeout_seconds: int = HF_MODEL_DOWNLOAD_TIMEOUT_SECONDS,
) -> dict[str, object]:
    if source not in {SensorySource.SPEECH, SensorySource.SOUND}:
        raise RuntimeError("llama.cpp 一键准备仅适用于语音和声音事件。")
    recommendation = recommended_llama_cpp_audio_model(source)
    if recommendation is None:
        raise RuntimeError(f"{source.value} 没有内置推荐 llama.cpp 音频模型。")
    runtime_payload = _ensure_llama_cpp_runtime(
        Path(base_dir),
        timeout_seconds=timeout_seconds,
    )
    repo_id = llama_cpp_audio_model_repo_id(recommendation.model)
    local_dir = StoragePaths(base_dir).sensory_model_cache_for(source.value, repo_id)
    cached_before = llama_cpp_audio_cache_ready(local_dir, recommendation.include_patterns)
    download_result: dict[str, object] = {}
    if not cached_before:
        if not download_model:
            raise RuntimeError(
                f"推荐模型 {recommendation.model} 尚未缓存；确认后才能下载 {recommendation.download_hint}。"
            )
        download_result = download_huggingface_model(
            repo_id,
            local_dir,
            include_patterns=recommendation.include_patterns,
            timeout_seconds=timeout_seconds,
        )
    gguf_count = _gguf_count(local_dir)
    model_payload: dict[str, object] = {
        "repo_id": repo_id,
        "model": recommendation.model,
        "local_dir": str(local_dir),
        "download_hint": recommendation.download_hint,
        "estimated_download_bytes": recommendation.estimated_download_bytes,
        "include_patterns": list(recommendation.include_patterns),
        "cached_before": cached_before,
        "downloaded": not cached_before,
        "gguf_count": gguf_count,
    }
    if download_result:
        model_payload["download_message"] = str(download_result.get("message") or "")
    if not llama_cpp_audio_cache_ready(local_dir, recommendation.include_patterns):
        raise RuntimeError(
            f"推荐模型 {recommendation.model} 下载后未找到 GGUF 文件，请检查 Hugging Face 仓库文件或 include patterns。"
        )
    return {
        "ok": True,
        "source": source.value,
        "runtime": runtime_payload,
        "model": model_payload,
        "message": "llama.cpp 音频后端已准备好。",
    }


def _ensure_llama_cpp_runtime(
    base_dir: Path,
    *,
    timeout_seconds: int,
) -> dict[str, object]:
    existing = discover_llama_server_binary(base_dir)
    if existing:
        return {
            "binary_path": existing,
            "install_dir": str(Path(existing).parent),
            "already_installed": True,
            "platform_key": llama_cpp_platform_key(),
            "message": "已找到可用的 llama-server。",
        }
    catalog = fetch_llama_cpp_runtime_package_catalog(
        base_dir=base_dir,
        timeout_seconds=30,
    )
    package = select_llama_cpp_runtime_package(catalog.packages)
    result = install_llama_cpp_runtime_package(
        base_dir,
        package,
        timeout_seconds=timeout_seconds,
    )
    payload = result.to_mapping()
    payload["platform_key"] = llama_cpp_platform_key()
    payload["package_source"] = catalog.source
    return payload


def _gguf_count(path: Path) -> int:
    try:
        return len(list(Path(path).rglob("*.gguf"))) if Path(path).is_dir() else 0
    except OSError:
        return 0


def _run_hf_command(
    args: list[str],
    *,
    timeout_seconds: int,
) -> subprocess.CompletedProcess[str]:
    executable = shutil.which("hf")
    if not executable:
        raise RuntimeError(HF_CLI_INSTALL_HINT)
    command = [executable, *args]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Hugging Face 操作超时，请检查网络或稍后重试。") from exc
    except OSError as exc:
        raise RuntimeError(str(exc)) from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(detail or f"`hf {' '.join(args)}` 执行失败。")
    return completed


def _parse_huggingface_model_results(raw_text: str) -> list[dict[str, object]]:
    text = raw_text.strip()
    if not text:
        return []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Hugging Face CLI 返回的模型列表不是 JSON。") from exc
    if isinstance(payload, dict):
        raw_items = payload.get("models") or payload.get("data") or payload.get("items") or []
    elif isinstance(payload, list):
        raw_items = payload
    else:
        raw_items = []
    results: list[dict[str, object]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        repo_id = item.get("id") or item.get("modelId") or item.get("name")
        if not isinstance(repo_id, str) or "/" not in repo_id:
            continue
        result: dict[str, object] = {"repo_id": repo_id.strip()}
        for key in ("pipeline_tag", "downloads", "likes", "lastModified", "tags", "library_name"):
            if key in item:
                result[key] = item[key]
        results.append(result)
    return results


def _mark_huggingface_model_compatibility(
    source: SensorySource,
    models: list[dict[str, object]],
) -> list[dict[str, object]]:
    return [
        {
            **model,
            **_huggingface_model_compatibility(source, model),
        }
        for model in models
    ]


def _huggingface_model_compatibility(
    source: SensorySource,
    model: dict[str, object],
) -> dict[str, str]:
    pipeline_tag = str(model.get("pipeline_tag") or "").strip().lower()
    tags = _normalized_huggingface_tags(model)
    haystack = " ".join(
        [
            str(model.get("repo_id") or ""),
            pipeline_tag,
            " ".join(tags),
            str(model.get("library_name") or ""),
        ]
    ).lower()
    clear_tasks = _clear_huggingface_tasks(source)
    possible_markers = _possible_huggingface_markers(source)
    if pipeline_tag in clear_tasks:
        return {
            "compatibility": HF_COMPATIBILITY_CLEAR,
            "compatibility_label": "明显兼容",
            "compatibility_reason": f"主任务 {pipeline_tag}",
        }
    if not pipeline_tag and tags.intersection(clear_tasks):
        task = sorted(tags.intersection(clear_tasks))[0]
        return {
            "compatibility": HF_COMPATIBILITY_CLEAR,
            "compatibility_label": "明显兼容",
            "compatibility_reason": f"任务标签 {task}",
        }
    if any(marker in haystack for marker in possible_markers):
        if pipeline_tag:
            reason = f"命名/标签匹配，主任务 {pipeline_tag}"
        else:
            reason = "命名/标签匹配，未声明主任务"
        return {
            "compatibility": HF_COMPATIBILITY_POSSIBLE,
            "compatibility_label": "可能兼容",
            "compatibility_reason": reason,
        }
    return {
        "compatibility": HF_COMPATIBILITY_UNKNOWN,
        "compatibility_label": "类型未验证",
        "compatibility_reason": "未发现明确任务标签",
    }


def _normalized_huggingface_tags(model: dict[str, object]) -> set[str]:
    raw_tags = model.get("tags")
    if not isinstance(raw_tags, list):
        return set()
    return {
        str(tag).strip().lower()
        for tag in raw_tags
        if str(tag).strip()
    }


def _clear_huggingface_tasks(source: SensorySource) -> set[str]:
    if source == SensorySource.SPEECH:
        return {"automatic-speech-recognition", "audio-text-to-text"}
    if source == SensorySource.SOUND:
        return {"audio-classification"}
    return {
        "image-text-to-text",
        "visual-question-answering",
        "image-to-text",
        "document-question-answering",
    }


def _possible_huggingface_markers(source: SensorySource) -> tuple[str, ...]:
    if source == SensorySource.SPEECH:
        return ("whisper", "faster-whisper", "asr", "speech", "sensevoice", "wav2vec")
    if source == SensorySource.SOUND:
        return ("audio-classification", "sound-event", "yamnet", "audio-spectrogram", "panns")
    return (
        "vision-language",
        "vlm",
        "qwen-vl",
        "qwen2-vl",
        "qwen2.5-vl",
        "qwen2_5_vl",
        "qwen3-vl",
        "qwen3_vl",
        "llava",
        "internvl",
        "minicpm-v",
        "molmo",
        "image-text-to-text",
    )


def _sort_huggingface_model_results(models: list[dict[str, object]]) -> list[dict[str, object]]:
    order = {
        HF_COMPATIBILITY_CLEAR: 0,
        HF_COMPATIBILITY_POSSIBLE: 1,
        HF_COMPATIBILITY_UNKNOWN: 2,
    }
    return sorted(
        models,
        key=lambda model: (
            order.get(str(model.get("compatibility") or ""), 3),
            -int(model.get("downloads") or 0)
            if isinstance(model.get("downloads"), int)
            else 0,
            str(model.get("repo_id") or "").lower(),
        ),
    )


def _get_json(url: str, timeout_seconds: int, *, api_key: str = "") -> dict[str, object]:
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body}") from exc
    except (OSError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(str(exc)) from exc
    if not isinstance(data, dict):
        raise RuntimeError("模型列表接口返回的不是 JSON object。")
    return data


def _default_sensory_endpoint(config: SensoryProviderConfig) -> str:
    endpoint = config.endpoint.strip()
    if endpoint:
        return endpoint
    backend = str(config.extra.get("backend") or config.extra.get("provider") or "").strip().lower()
    if backend in {"lmstudio", "lm_studio"}:
        return DEFAULT_LMSTUDIO_ENDPOINT
    if backend in {"llama", "llama.cpp", "llama_cpp", "llamacpp"}:
        return DEFAULT_LLAMA_CPP_ENDPOINT
    if backend == "ollama":
        return DEFAULT_OLLAMA_ENDPOINT
    return endpoint


def _openai_models_url(endpoint: str) -> str:
    base = endpoint.strip().rstrip("/")
    if not base:
        raise RuntimeError("请先填写增强感知服务 Endpoint。")
    if base.endswith("/models"):
        return base
    return f"{base}/models"


def _ollama_tags_url(endpoint: str) -> str:
    base = (endpoint.strip() or DEFAULT_OLLAMA_ENDPOINT).rstrip("/")
    if base.endswith("/api/tags"):
        return base
    return f"{base}/api/tags"


def _sensory_test_text(source: SensorySource) -> str:
    if source == SensorySource.SPEECH:
        return "请判断这段短测试音频中是否有人声，并返回结构化 JSON。"
    if source == SensorySource.SOUND:
        return "请识别这段短测试音频中的声音类型，并返回结构化 JSON。"
    return "请识别这张测试图片并返回结构化 JSON。"


def _sensory_test_media_ref(source: SensorySource) -> str:
    if source == SensorySource.VISION:
        return _SENSORY_TEST_IMAGE_DATA_URL
    if source in {SensorySource.SPEECH, SensorySource.SOUND}:
        return _SENSORY_TEST_AUDIO_DATA_URL
    return ""


class MemoryListWorker(QObject):
    succeeded = Signal(list)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, memory_store: MemoryStore, limit: int | None = None) -> None:
        super().__init__()
        self.memory_store = memory_store
        self.limit = limit

    @Slot()
    def run(self) -> None:
        try:
            memories = self.memory_store.list_memories(limit=self.limit)
        except Exception as exc:  # UI 边界统一转成可读错误。
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit(memories)
        finally:
            self.finished.emit()


class MemoryModelImportWorker(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, memory_store: MemoryStore, archive_path: Path) -> None:
        super().__init__()
        self.memory_store = memory_store
        self.archive_path = archive_path

    @Slot()
    def run(self) -> None:
        try:
            result = self.memory_store.import_embedding_model_archive(self.archive_path)
        except Exception as exc:  # UI 边界统一转成可读错误。
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit(result)
        finally:
            self.finished.emit()


class MemoryModelDownloadWorker(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, memory_store: MemoryStore) -> None:
        super().__init__()
        self.memory_store = memory_store

    @Slot()
    def run(self) -> None:
        try:
            result = self.memory_store.download_embedding_model()
        except Exception as exc:  # UI 边界统一转成可读错误。
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit(result)
        finally:
            self.finished.emit()


class BackchannelModelImportWorker(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(
        self,
        base_dir: Path,
        archive_path: Path,
        import_model: Callable[[Path, Path], object] = import_backchannel_model_archive,
    ) -> None:
        super().__init__()
        self.base_dir = base_dir
        self.archive_path = archive_path
        self.import_model = import_model

    @Slot()
    def run(self) -> None:
        try:
            result = self.import_model(self.archive_path, self.base_dir)
        except Exception as exc:  # UI 边界统一转成可读错误。
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit(result)
        finally:
            self.finished.emit()


class BackchannelModelDownloadWorker(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(
        self,
        base_dir: Path,
        download_model: Callable[[Path], object] = download_backchannel_model,
    ) -> None:
        super().__init__()
        self.base_dir = base_dir
        self.download_model = download_model

    @Slot()
    def run(self) -> None:
        try:
            result = self.download_model(self.base_dir)
        except Exception as exc:  # UI 边界统一转成可读错误。
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit(result)
        finally:
            self.finished.emit()


class ThemeAiWorker(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, settings: ApiSettings, profile: CharacterProfile, *, ai_enabled: bool) -> None:
        super().__init__()
        self.settings = settings
        self.profile = profile
        self.ai_enabled = ai_enabled

    @Slot()
    def run(self) -> None:
        try:
            data_url = _image_file_to_data_url(self.profile.default_portrait_path)
            content = OpenAICompatibleClient(self.settings).complete_raw(
                build_theme_color_system_prompt(self.profile.display_name),
                [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "请根据这张角色默认立绘生成 Sakura 桌宠 UI 主题配色。只返回完整 JSON 对象，不要输出 Markdown 或解释。",
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": data_url,
                                    "detail": "low",
                                },
                            },
                        ],
                    }
                ],
                temperature=0.2,
                # thinking 模型不兼容 json_object，依赖 prompt 约束 JSON 输出
                max_tokens=2000,
            )
            self.succeeded.emit(parse_ai_theme_response(content, ai_enabled=self.ai_enabled))
        except Exception as exc:  # noqa: BLE001 - UI 边界统一转成可读错误。
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()


class CharacterArchiveExportWorker(QObject):
    succeeded = Signal(str)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, profile: CharacterProfile, output_path: Path, export_kind: Literal["full", "card", "voice"]) -> None:
        super().__init__()
        self.profile = profile
        self.output_path = output_path
        self.export_kind = export_kind

    @Slot()
    def run(self) -> None:
        try:
            if self.export_kind in ("full", "voice") and not _has_exportable_voice_model(self.profile):
                raise CharacterArchiveError("当前角色没有完整语音模型，请导出单角色包。")
            if self.export_kind == "voice":
                export_character_voice_archive(self.profile, self.output_path)
            else:
                export_character_archive(
                    self.profile,
                    self.output_path,
                    include_voice=self.export_kind == "full",
                )
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit(str(self.output_path))
        finally:
            self.finished.emit()


def _has_exportable_voice_model(profile: CharacterProfile | None) -> bool:
    """判断角色是否带有可随包导出的完整语音模型。"""

    if profile is None or profile.voice is None:
        return False
    return (
        profile.voice.gpt_model_path is not None
        and profile.voice.gpt_model_path.is_file()
        and profile.voice.sovits_model_path is not None
        and profile.voice.sovits_model_path.is_file()
    )
