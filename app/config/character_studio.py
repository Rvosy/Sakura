from __future__ import annotations

import json
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config.character_archive import export_character_archive
from app.config.character_loader import (
    THEME_SOURCE_PACKAGE,
    CharacterConfigError,
    CharacterProfile,
    CharacterRegistry,
    _load_profile,
    character_theme_to_mapping,
)
from app.ui.theme import DEFAULT_THEME_SETTINGS, ThemeSettings, theme_from_mapping, theme_to_mapping

CARD_FILENAME = "card.md"
DEFAULT_TONE_REFS = "voice/refs/ref.txt"
_CHARACTER_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_PORTRAIT_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


@dataclass
class VoiceDraft:
    """角色语音配置草稿，对应 character.json 的 voice 段。"""

    tone_refs: str = DEFAULT_TONE_REFS
    gpt_model: str | None = None
    sovits_model: str | None = None
    ref_lang: str = "ja"
    text_lang: str = "ja"


@dataclass
class CharacterStudioDoc:
    """角色包可编辑草稿，字段与 character.json/card.md 互相转换。"""

    id: str = ""
    display_name: str = ""
    initial_message: str = ""
    card_text: str = ""
    default_portrait: str = ""
    expressions: dict[str, str] = field(default_factory=dict)
    reply_tones: list[str] = field(default_factory=list)
    theme: ThemeSettings = DEFAULT_THEME_SETTINGS
    voice: VoiceDraft | None = None

    def to_manifest(self) -> dict[str, Any]:
        manifest: dict[str, Any] = {
            "id": self.id.strip(),
            "display_name": self.display_name.strip(),
            "card": CARD_FILENAME,
            "portrait": {
                "default": self.default_portrait.strip(),
                "expressions": {
                    str(label).strip(): str(path).strip()
                    for label, path in self.expressions.items()
                    if str(label).strip() and str(path).strip()
                },
            },
            "theme": character_theme_to_mapping(
                self.theme.normalized(),
                source=THEME_SOURCE_PACKAGE,
            ),
        }
        if self.initial_message.strip():
            manifest["initial_message"] = self.initial_message.strip()
        tones = [str(tone).strip() for tone in self.reply_tones if str(tone).strip()]
        if tones:
            manifest["reply"] = {"tones": tones}
        if self.voice is not None:
            voice: dict[str, Any] = {
                "tone_refs": self.voice.tone_refs,
                "ref_lang": self.voice.ref_lang,
                "text_lang": self.voice.text_lang,
            }
            if self.voice.gpt_model:
                voice["gpt_model"] = self.voice.gpt_model
            if self.voice.sovits_model:
                voice["sovits_model"] = self.voice.sovits_model
            manifest["voice"] = voice
        return manifest

    def manifest_json(self) -> str:
        return json.dumps(self.to_manifest(), ensure_ascii=False, indent=2)

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "initial_message": self.initial_message,
            "card_text": self.card_text,
            "default_portrait": self.default_portrait,
            "expressions": dict(self.expressions),
            "reply_tones": list(self.reply_tones),
            "theme": theme_to_mapping(self.theme.normalized()),
            "voice": None
            if self.voice is None
            else {
                "tone_refs": self.voice.tone_refs,
                "gpt_model": self.voice.gpt_model or "",
                "sovits_model": self.voice.sovits_model or "",
                "ref_lang": self.voice.ref_lang,
                "text_lang": self.voice.text_lang,
            },
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "CharacterStudioDoc":
        if not isinstance(payload, dict):
            raise ValueError("角色数据必须是对象。")
        raw_voice = payload.get("voice")
        voice = None
        if isinstance(raw_voice, dict):
            voice = VoiceDraft(
                tone_refs=str(raw_voice.get("tone_refs") or DEFAULT_TONE_REFS),
                gpt_model=str(raw_voice.get("gpt_model") or "") or None,
                sovits_model=str(raw_voice.get("sovits_model") or "") or None,
                ref_lang=str(raw_voice.get("ref_lang") or "ja"),
                text_lang=str(raw_voice.get("text_lang") or "ja"),
            )
        raw_expressions = payload.get("expressions")
        expressions = raw_expressions if isinstance(raw_expressions, dict) else {}
        raw_reply_tones = payload.get("reply_tones")
        reply_tones = raw_reply_tones if isinstance(raw_reply_tones, list) else []
        return cls(
            id=str(payload.get("id") or "").strip(),
            display_name=str(payload.get("display_name") or "").strip(),
            initial_message=str(payload.get("initial_message") or ""),
            card_text=str(payload.get("card_text") or ""),
            default_portrait=str(payload.get("default_portrait") or "").strip(),
            expressions={
                str(label).strip(): str(path).strip()
                for label, path in expressions.items()
                if str(label).strip() and str(path).strip()
            },
            reply_tones=[str(tone).strip() for tone in reply_tones if str(tone).strip()],
            theme=theme_from_mapping(payload.get("theme")).normalized(),
            voice=voice,
        )

    @classmethod
    def from_package_dir(cls, package_dir: Path) -> "CharacterStudioDoc":
        manifest_path = Path(package_dir) / "character.json"
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"character.json 必须是 JSON 对象：{manifest_path}")

        portrait = raw.get("portrait") if isinstance(raw.get("portrait"), dict) else {}
        expressions_raw = portrait.get("expressions") if isinstance(portrait.get("expressions"), dict) else {}
        reply = raw.get("reply") if isinstance(raw.get("reply"), dict) else {}
        tones_raw = reply.get("tones") if isinstance(reply.get("tones"), list) else []

        card_name = str(raw.get("card") or CARD_FILENAME)
        card_path = Path(package_dir) / card_name
        card_text = card_path.read_text(encoding="utf-8") if card_path.exists() else ""

        voice: VoiceDraft | None = None
        voice_raw = raw.get("voice")
        if isinstance(voice_raw, dict):
            voice = VoiceDraft(
                tone_refs=str(voice_raw.get("tone_refs") or DEFAULT_TONE_REFS),
                gpt_model=str(voice_raw.get("gpt_model") or "") or None,
                sovits_model=str(voice_raw.get("sovits_model") or "") or None,
                ref_lang=str(voice_raw.get("ref_lang") or "ja"),
                text_lang=str(voice_raw.get("text_lang") or "ja"),
            )

        return cls(
            id=str(raw.get("id") or ""),
            display_name=str(raw.get("display_name") or ""),
            initial_message=str(raw.get("initial_message") or ""),
            card_text=card_text,
            default_portrait=str(portrait.get("default") or ""),
            expressions={
                str(label): str(path)
                for label, path in expressions_raw.items()
                if isinstance(label, str) and isinstance(path, str)
            },
            reply_tones=[str(tone) for tone in tones_raw if isinstance(tone, str) and tone.strip()],
            theme=theme_from_mapping(raw.get("theme")).normalized(),
            voice=voice,
        )


class CharacterStudioService:
    """角色工作室后端服务：草稿编辑与本地角色包保存。"""

    def __init__(self, base_dir: Path, workspace_root: Path | None = None) -> None:
        self.base_dir = Path(base_dir)
        self.characters_dir = self.base_dir / "characters"
        self.workspace_root = (
            Path(workspace_root)
            if workspace_root is not None
            else self.base_dir / "runtime" / "character-studio" / "workspace"
        )
        self.workspace_characters_dir = self.workspace_root / "characters"
        self.backup_root = self.base_dir / "runtime" / "character-studio" / "backups"
        self.workspace_characters_dir.mkdir(parents=True, exist_ok=True)
        self.backup_root.mkdir(parents=True, exist_ok=True)

    def list_characters(self, *, current_character_id: str = "") -> list[dict[str, Any]]:
        try:
            profiles = CharacterRegistry(self.base_dir).all()
        except CharacterConfigError:
            profiles = []
        items = [
            self._summary_from_profile(profile, current_character_id)
            for profile in profiles
        ]
        items.sort(key=lambda item: (not item["is_current"], item["display_name"].casefold(), item["id"]))
        return items

    def open_character(self, character_id: str) -> dict[str, Any]:
        safe_id = _validate_character_id(character_id)
        profile = CharacterRegistry(self.base_dir).get(safe_id)
        package_dir = self._draft_package_dir(safe_id)
        if package_dir.exists():
            shutil.rmtree(package_dir)
        shutil.copytree(profile.package_dir, package_dir)
        _validate_package_local_paths(package_dir)
        doc = CharacterStudioDoc.from_package_dir(package_dir)
        return self._opened_payload(package_dir, doc, source="installed")

    def create_character(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("角色数据必须是对象。")
        safe_id = _validate_character_id(str(payload.get("id") or ""))
        display_name = str(payload.get("display_name") or safe_id).strip() or safe_id
        package_dir = self._draft_package_dir(safe_id)
        if package_dir.exists():
            shutil.rmtree(package_dir)
        (package_dir / "portraits").mkdir(parents=True)
        (package_dir / CARD_FILENAME).write_text("", encoding="utf-8")
        doc = CharacterStudioDoc(id=safe_id, display_name=display_name)
        self.save_draft(doc.to_payload(), package_dir)
        return self._opened_payload(package_dir, doc, source="draft")

    def save_draft(self, doc_payload: dict[str, Any], package_dir: Path) -> dict[str, Any]:
        package_dir = self._require_workspace_package(package_dir)
        doc = CharacterStudioDoc.from_payload(doc_payload)
        _validate_character_id(doc.id)
        package_dir.mkdir(parents=True, exist_ok=True)
        (package_dir / CARD_FILENAME).write_text(doc.card_text, encoding="utf-8")
        (package_dir / "character.json").write_text(doc.manifest_json(), encoding="utf-8")
        return self._opened_payload(package_dir, doc, source="draft")

    def save_character(
        self,
        doc_payload: dict[str, Any],
        package_dir: Path,
        *,
        current_character_id: str = "",
    ) -> dict[str, Any]:
        saved = self.save_draft(doc_payload, package_dir)
        draft_dir = Path(saved["package_dir"])
        profile = self.validate_draft(draft_dir)
        target_dir = self.characters_dir / profile.id
        staging_dir = self.characters_dir / f".{profile.id}.studio-{uuid.uuid4().hex}"
        if staging_dir.exists():
            shutil.rmtree(staging_dir)
        shutil.copytree(draft_dir, staging_dir)
        backup_dir = self._backup_target(target_dir)
        try:
            if target_dir.exists():
                shutil.rmtree(target_dir)
            shutil.move(str(staging_dir), str(target_dir))
        except Exception:
            if target_dir.exists():
                shutil.rmtree(target_dir)
            if backup_dir is not None and backup_dir.exists():
                shutil.copytree(backup_dir, target_dir)
            raise
        finally:
            if staging_dir.exists():
                shutil.rmtree(staging_dir)

        registry = CharacterRegistry(self.base_dir)
        saved_profile = registry.get(profile.id)
        return {
            "saved_character_id": profile.id,
            "current_character_id": str(current_character_id or ""),
            "characters": self.list_characters(current_character_id=str(current_character_id or "")),
            "doc": CharacterStudioDoc.from_package_dir(target_dir).to_payload(),
            "package_dir": str(draft_dir),
            "message": f"已保存角色「{saved_profile.display_name}」。",
        }

    def import_portrait(self, package_dir: Path, source_path: Path, *, label: str) -> dict[str, str]:
        package_dir = self._require_workspace_package(package_dir)
        source = Path(source_path)
        if source.suffix.lower() not in _PORTRAIT_SUFFIXES:
            raise ValueError("立绘文件扩展名必须是 .png / .jpg / .jpeg / .webp / .gif。")
        if not source.is_file():
            raise ValueError(f"立绘文件不存在：{source}")
        portraits_dir = package_dir / "portraits"
        portraits_dir.mkdir(parents=True, exist_ok=True)
        safe_label = _safe_filename(label or source.stem)
        target = portraits_dir / f"{safe_label}{source.suffix.lower()}"
        if target.exists():
            target = portraits_dir / f"{safe_label}-{uuid.uuid4().hex[:8]}{source.suffix.lower()}"
        shutil.copy2(source, target)
        return {
            "relative_path": target.relative_to(package_dir).as_posix(),
            "path": str(target),
        }

    def validate_draft(self, package_dir: Path) -> CharacterProfile:
        package_dir = self._require_workspace_package(package_dir)
        _validate_package_local_paths(package_dir)
        return _load_profile(package_dir / "character.json")

    def export_archive(self, package_dir: Path, output_path: Path, *, include_voice: bool) -> dict[str, str]:
        profile = self.validate_draft(package_dir)
        output = Path(output_path)
        output = output if output.suffix.lower() == ".char" else output.with_suffix(".char")
        parent = output.parent
        if parent and not parent.exists():
            raise ValueError(f"导出目录不存在：{parent}")
        export_character_archive(profile, output, include_voice=include_voice)
        return {
            "output_path": str(output),
            "message": f"角色包已导出到：{output}",
        }

    def _draft_package_dir(self, character_id: str) -> Path:
        return self.workspace_characters_dir / _validate_character_id(character_id)

    def _opened_payload(self, package_dir: Path, doc: CharacterStudioDoc, *, source: str) -> dict[str, Any]:
        return {
            "package_dir": str(package_dir),
            "source": source,
            "doc": doc.to_payload(),
            "characters": self.list_characters(current_character_id=doc.id),
        }

    def _summary_from_profile(self, profile: CharacterProfile, current_character_id: str) -> dict[str, Any]:
        theme = (profile.theme_settings or DEFAULT_THEME_SETTINGS).normalized()
        return {
            "id": profile.id,
            "display_name": profile.display_name,
            "package_dir": str(profile.package_dir),
            "is_current": profile.id == current_character_id,
            "has_voice": profile.voice is not None,
            "source": "installed",
            "theme": theme_to_mapping(theme),
            "default_portrait": str(profile.default_portrait_path),
        }

    def _require_workspace_package(self, package_dir: Path) -> Path:
        path = Path(package_dir)
        resolved = path.resolve()
        workspace = self.workspace_characters_dir.resolve()
        try:
            resolved.relative_to(workspace)
        except ValueError as exc:
            raise ValueError(f"草稿目录必须位于角色工作室工作区：{path}") from exc
        return path

    def _backup_target(self, target_dir: Path) -> Path | None:
        if not target_dir.exists():
            return None
        backup_dir = self.backup_root / f"{target_dir.name}-{time.strftime('%Y%m%d-%H%M%S')}"
        if backup_dir.exists():
            backup_dir = self.backup_root / f"{backup_dir.name}-{uuid.uuid4().hex[:8]}"
        shutil.copytree(target_dir, backup_dir)
        return backup_dir


def _validate_character_id(value: str) -> str:
    character_id = str(value or "").strip()
    if not character_id or not _CHARACTER_ID_RE.fullmatch(character_id):
        raise ValueError("角色 id 只能包含字母、数字、下划线、点和横线。")
    return character_id


def _safe_filename(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip()).strip(".-")
    return text or "portrait"


def _validate_package_local_paths(package_dir: Path) -> None:
    manifest_path = Path(package_dir) / "character.json"
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"角色清单无法读取：{manifest_path}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"角色清单必须是 JSON 对象：{manifest_path}")

    _check_local_path(package_dir, raw.get("card"), "角色卡")
    portrait = raw.get("portrait")
    if isinstance(portrait, dict):
        _check_local_path(package_dir, portrait.get("default"), "默认立绘")
        expressions = portrait.get("expressions")
        if isinstance(expressions, dict):
            for label, path_text in expressions.items():
                _check_local_path(package_dir, path_text, f"{label} 表情立绘")
    voice = raw.get("voice")
    if isinstance(voice, dict):
        _check_local_path(package_dir, voice.get("tone_refs"), "语气参考表")
        _check_local_path(package_dir, voice.get("gpt_model"), "GPT 模型")
        _check_local_path(package_dir, voice.get("sovits_model"), "SoVITS 模型")


def _check_local_path(package_dir: Path, value: object, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        return
    path = Path(value.strip().strip('"').strip("'"))
    if path.is_absolute():
        raise ValueError(f"{label}不能使用绝对路径：{value}")
    try:
        (Path(package_dir) / path).resolve().relative_to(Path(package_dir).resolve())
    except ValueError as exc:
        raise ValueError(f"{label}不能指向角色包外：{value}") from exc
