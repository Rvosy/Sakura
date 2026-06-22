"""app/ui/settings/workers.py — 设置窗口的后台 Worker。

从 settings_dialog.py 拆出：API 连通性测试、模型列表探测、TTS 试听、
记忆列表加载、嵌入模型导入、主题 AI 生成、角色包导出。
全部为纯 QObject worker，不持有任何设置页控件。
"""

from __future__ import annotations

import base64
import mimetypes
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
from app.ui.theme import parse_ai_theme_response
from app.voice.factory import create_tts_provider
from app.voice.tts_settings import GPTSoVITSTTSSettings


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
