from __future__ import annotations

import shutil
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


class VoiceInputModelError(RuntimeError):
    """voice_input 模型管理错误。"""


@dataclass(frozen=True)
class VoiceInputModelSpec:
    model_name: str
    label: str
    repo_id: str
    description: str


MODEL_SPECS: dict[str, VoiceInputModelSpec] = {
    "tiny": VoiceInputModelSpec(
        model_name="tiny",
        label="Tiny",
        repo_id="Systran/faster-whisper-tiny",
        description="速度最快，占用最低，适合实时语音输入。",
    ),
    "base": VoiceInputModelSpec(
        model_name="base",
        label="Base",
        repo_id="Systran/faster-whisper-base",
        description="准确率更高，CPU 上仍可接受。",
    ),
    "small": VoiceInputModelSpec(
        model_name="small",
        label="Small",
        repo_id="Systran/faster-whisper-small",
        description="准确率较高，但下载和识别耗时更长。",
    ),
}


def models_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "models"


def model_dir(data_dir: Path, model_name: str) -> Path:
    return models_dir(data_dir) / safe_model_name(model_name)


def temp_audio_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "temp_audio"


def safe_model_name(model_name: str) -> str:
    text = str(model_name or "").strip().lower()
    if text in MODEL_SPECS:
        return text
    return "tiny"


def model_available(data_dir: Path, model_name: str) -> bool:
    path = model_dir(data_dir, model_name)
    if not path.is_dir():
        return False
    return any(child.is_file() for child in path.rglob("*"))


def model_status_text(data_dir: Path, model_name: str) -> str:
    name = safe_model_name(model_name)
    spec = MODEL_SPECS[name]
    if model_available(data_dir, name):
        return f"模型 {spec.label} 已安装：{model_dir(data_dir, name)}"
    return f"模型 {spec.label} 未安装。请先下载模型后再录音识别。"


def download_model(
    data_dir: Path,
    model_name: str,
    *,
    cancel_event: threading.Event | None = None,
    status_callback: Callable[[str], None] | None = None,
) -> Path:
    name = safe_model_name(model_name)
    spec = MODEL_SPECS[name]
    root = models_dir(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    target = model_dir(data_dir, name)
    temp_parent = Path(
        tempfile.mkdtemp(
            prefix=f".{name}.download-",
            dir=root,
        )
    )
    try:
        if cancel_event is not None and cancel_event.is_set():
            raise VoiceInputModelError("模型下载已取消。")
        if status_callback is not None:
            status_callback(f"正在下载 {spec.label} 模型...")
        try:
            from huggingface_hub import snapshot_download
        except ImportError as exc:
            raise VoiceInputModelError("缺少 huggingface_hub 依赖，无法下载 ASR 模型。") from exc
        snapshot_download(
            repo_id=spec.repo_id,
            local_dir=str(temp_parent),
            local_dir_use_symlinks=False,
        )
        if cancel_event is not None and cancel_event.is_set():
            raise VoiceInputModelError("模型下载已取消。")
        if target.exists():
            shutil.rmtree(target)
        temp_parent.replace(target)
        if status_callback is not None:
            status_callback(f"模型 {spec.label} 已下载。")
        return target
    except Exception:
        shutil.rmtree(temp_parent, ignore_errors=True)
        raise
