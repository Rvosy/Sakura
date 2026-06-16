"""CharacterDoc —— 角色包的内存草稿模型。

负责在编辑器内承载一份角色包的可编辑状态，并与磁盘上的 character.json /
card.md 互相转换。序列化结构对齐 app/config/character_archive.py 导出时构建的
character_manifest，确保保存后能被 app.config.character_loader 正常加载。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config.character_loader import THEME_SOURCE_PACKAGE, character_theme_to_mapping
from app.ui.theme import DEFAULT_THEME_SETTINGS, ThemeSettings, theme_from_mapping

# 人格卡固定文件名；打开旧包若用其他名，读入文本后统一写回 card.md
CARD_FILENAME = "card.md"
DEFAULT_TONE_REFS = "voice/refs/ref.txt"


@dataclass
class VoiceDraft:
    """语音配置草稿，对应 character.json 的 voice 段。"""

    tone_refs: str = DEFAULT_TONE_REFS
    gpt_model: str | None = None
    sovits_model: str | None = None
    ref_lang: str = "ja"
    text_lang: str = "ja"


@dataclass
class CharacterDoc:
    """一份角色包的可编辑草稿。所有资源路径均为相对包目录的 POSIX 风格相对路径。"""

    id: str = ""
    display_name: str = ""
    initial_message: str = ""
    card_text: str = ""
    default_portrait: str = ""
    # 立绘描述标签 → 立绘相对路径（portrait.expressions）
    expressions: dict[str, str] = field(default_factory=dict)
    # 回复语气列表（reply.tones），与立绘表情标签相互独立
    reply_tones: list[str] = field(default_factory=list)
    theme: ThemeSettings = DEFAULT_THEME_SETTINGS
    voice: VoiceDraft | None = None

    def to_manifest(self) -> dict[str, Any]:
        """序列化为 character.json 的 dict（结构对齐归档导出）。"""
        manifest: dict[str, Any] = {
            "id": self.id.strip(),
            "display_name": self.display_name.strip(),
        }
        if self.initial_message.strip():
            manifest["initial_message"] = self.initial_message.strip()
        manifest["card"] = CARD_FILENAME
        manifest["portrait"] = {
            "default": self.default_portrait,
            "expressions": dict(self.expressions),
        }
        tones = [tone.strip() for tone in self.reply_tones if tone.strip()]
        if tones:
            manifest["reply"] = {"tones": tones}
        # theme.source 固定为 package：角色包自带配色
        manifest["theme"] = character_theme_to_mapping(self.theme, source=THEME_SOURCE_PACKAGE)
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

    @classmethod
    def from_package_dir(cls, package_dir: Path) -> "CharacterDoc":
        """从一个角色包目录读出草稿（不强制校验文件存在，便于打开问题包修复）。"""
        manifest_path = package_dir / "character.json"
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"character.json 必须是 JSON 对象：{manifest_path}")

        portrait = raw.get("portrait") if isinstance(raw.get("portrait"), dict) else {}
        expressions_raw = portrait.get("expressions") if isinstance(portrait.get("expressions"), dict) else {}
        reply = raw.get("reply") if isinstance(raw.get("reply"), dict) else {}
        tones_raw = reply.get("tones") if isinstance(reply.get("tones"), list) else []

        card_name = str(raw.get("card") or CARD_FILENAME)
        card_path = package_dir / card_name
        card_text = card_path.read_text(encoding="utf-8") if card_path.exists() else ""

        theme = theme_from_mapping(raw.get("theme")).normalized()

        voice: VoiceDraft | None = None
        voice_raw = raw.get("voice")
        if isinstance(voice_raw, dict):
            voice = VoiceDraft(
                tone_refs=str(voice_raw.get("tone_refs") or DEFAULT_TONE_REFS),
                gpt_model=(str(voice_raw["gpt_model"]) if voice_raw.get("gpt_model") else None),
                sovits_model=(str(voice_raw["sovits_model"]) if voice_raw.get("sovits_model") else None),
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
                str(k): str(v)
                for k, v in expressions_raw.items()
                if isinstance(k, str) and isinstance(v, str)
            },
            reply_tones=[str(t) for t in tones_raw if isinstance(t, str) and t.strip()],
            theme=theme,
            voice=voice,
        )
