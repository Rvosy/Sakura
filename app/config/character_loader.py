from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from app.core.runtime_log import log_event
from app.llm.prompt_templates import with_desktop_pet_context
from app.storage.paths import sanitize_file_stem

if TYPE_CHECKING:
    from app.ui.theme import ThemeSettings


DEFAULT_CHARACTER_ID = "sakura"
DEFAULT_TONES = ["中性", "不满", "害羞", "请求", "困惑", "惊讶"]
FALLBACK_SYSTEM_PROMPT = """你是夜乃桜，一个冷静、克制、可靠的桌宠陪伴人格。
用户需要中文解释、开发或调试时，可以使用中文。"""
THEME_SOURCE_PACKAGE = "package"
THEME_SOURCE_COMPAT_DEFAULT = "compat_default"
CharacterThemeSource = Literal["package", "compat_default"]


class CharacterConfigError(RuntimeError):
    """角色包配置缺失或格式错误。"""


@dataclass(frozen=True)
class CharacterLoadIssue:
    manifest_path: Path
    error: str


@dataclass(frozen=True)
class CharacterVoice:
    gpt_model_path: Path | None
    sovits_model_path: Path | None
    tone_ref_path: Path
    ref_lang: str = "ja"
    text_lang: str = "ja"


@dataclass(frozen=True)
class CharacterProfile:
    id: str
    display_name: str
    package_dir: Path
    card_path: Path
    initial_message: str
    default_portrait_path: Path
    expression_portraits: dict[str, Path] = field(default_factory=dict)
    voice: CharacterVoice | None = None
    # 接话模板清单路径(可选,缺省即该角色 opt-out)。此处只解析路径不校验存在,
    # 文件缺失/非法由 manifest 加载方降级处理,不应让整个角色包加载失败。
    backchannel_manifest_path: Path | None = None
    reply_tones: list[str] = field(default_factory=lambda: [*DEFAULT_TONES])
    theme_settings: ThemeSettings | None = None
    theme_source: CharacterThemeSource = THEME_SOURCE_COMPAT_DEFAULT
    # 角色渲染后端配置（renderer 段原样保留；路径解析交由对应渲染插件处理）。
    renderer_config: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.theme_settings is None:
            object.__setattr__(self, "theme_settings", _default_theme_settings())

    @property
    def portrait_choices(self) -> list[str]:
        return list(self.expression_portraits)

    def portrait_for_tone(self, tone: str | None) -> Path:
        tone_key = (tone or "").strip()
        if tone_key and tone_key in self.expression_portraits:
            return self.expression_portraits[tone_key]
        return self.default_portrait_path

    def portrait_for_segment(self, portrait: str | None, tone: str | None = None) -> Path:
        portrait_key = (portrait or "").strip()
        if portrait_key and portrait_key in self.expression_portraits:
            return self.expression_portraits[portrait_key]
        return self.portrait_for_tone(tone)


class CharacterRegistry:
    """扫描并管理 characters/<角色id>/character.json 角色包。"""

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.characters_dir = base_dir / "characters"
        self.load_errors: tuple[CharacterLoadIssue, ...] = ()
        self.profiles = self._load_profiles()

    def all(self) -> list[CharacterProfile]:
        return sorted(self.profiles.values(), key=lambda profile: profile.display_name)

    def get(self, character_id: str) -> CharacterProfile:
        profile = self.profiles.get(character_id)
        if profile is None:
            raise CharacterConfigError(f"未找到角色包：{character_id}")
        return profile

    def _load_profiles(self) -> dict[str, CharacterProfile]:
        if not self.characters_dir.exists():
            raise CharacterConfigError(f"角色包目录不存在：{self.characters_dir}")

        profiles: dict[str, CharacterProfile] = {}
        storage_keys: dict[str, str] = {}
        issues: list[CharacterLoadIssue] = []
        for manifest_path in sorted(self.characters_dir.glob("*/character.json")):
            try:
                profile = _load_profile(manifest_path)
                if profile.id in profiles:
                    raise CharacterConfigError(f"角色 id 重复：{profile.id}")
                storage_key = sanitize_file_stem(profile.id).casefold()
                if storage_key in storage_keys:
                    raise CharacterConfigError(
                        f"角色 id 的存储键与 {storage_keys[storage_key]} 冲突：{profile.id}"
                    )
            except CharacterConfigError as exc:
                issue = CharacterLoadIssue(manifest_path=manifest_path, error=str(exc))
                issues.append(issue)
                log_event(
                    "Character",
                    "跳过损坏或不安全的角色包",
                    {"manifest": str(manifest_path), "error": str(exc)},
                )
                continue
            profiles[profile.id] = profile
            storage_keys[storage_key] = profile.id

        self.load_errors = tuple(issues)
        if not profiles:
            detail = f"；首个错误：{issues[0].error}" if issues else ""
            raise CharacterConfigError(f"未在 {self.characters_dir} 下找到可用角色包{detail}。")
        return profiles


def load_system_prompt(path: Path) -> str:
    if not path.exists():
        return _append_desktop_context(FALLBACK_SYSTEM_PROMPT)

    try:
        content = path.read_text(encoding="utf-8").strip()
    except OSError:
        return _append_desktop_context(FALLBACK_SYSTEM_PROMPT)

    if not content:
        return _append_desktop_context(FALLBACK_SYSTEM_PROMPT)

    return _append_desktop_context(content)


def load_character_system_prompt(profile: CharacterProfile) -> str:
    return load_system_prompt(profile.card_path)


def _load_profile(manifest_path: Path) -> CharacterProfile:
    package_dir = manifest_path.parent
    try:
        raw_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CharacterConfigError(f"角色清单无法读取：{manifest_path}") from exc
    if not isinstance(raw_data, dict):
        raise CharacterConfigError(f"角色清单必须是 JSON 对象：{manifest_path}")

    character_id = _validate_character_id(_required_text(raw_data, "id", manifest_path), manifest_path)
    display_name = _required_text(raw_data, "display_name", manifest_path)
    initial_message = _optional_text(raw_data, "initial_message", "……起動した。用事があるなら、呼んで。")
    card_path = _resolve_required_file(package_dir, _required_text(raw_data, "card", manifest_path), "角色卡")

    portrait_data = _required_dict(raw_data, "portrait", manifest_path)
    default_portrait = _resolve_required_file(
        package_dir,
        _required_text(portrait_data, "default", manifest_path),
        "默认立绘",
    )
    expression_portraits = _load_expression_portraits(package_dir, portrait_data)

    reply_data = raw_data.get("reply")
    reply_tones = _load_reply_tones(reply_data)
    voice = _load_voice(package_dir, raw_data.get("voice"), manifest_path)
    backchannel_text = _optional_text(raw_data, "backchannel", "")
    backchannel_manifest_path = (
        _resolve_package_path(package_dir, backchannel_text) if backchannel_text.strip() else None
    )
    theme_settings, theme_source, _missing_theme = character_theme_from_mapping(raw_data.get("theme"))

    return CharacterProfile(
        id=character_id,
        display_name=display_name,
        package_dir=package_dir,
        card_path=card_path,
        initial_message=initial_message,
        default_portrait_path=default_portrait,
        expression_portraits=expression_portraits,
        voice=voice,
        backchannel_manifest_path=backchannel_manifest_path,
        reply_tones=reply_tones,
        theme_settings=theme_settings,
        theme_source=theme_source,
        renderer_config=_load_renderer_config(raw_data),
    )


def character_theme_from_mapping(data: Any) -> tuple[ThemeSettings, CharacterThemeSource, bool]:
    from app.ui.theme import ThemeSettings, theme_colors_to_mapping, theme_from_mapping

    if isinstance(data, dict):
        source = _theme_source_from_text(data.get("source"))
        theme = theme_from_mapping(data).normalized()
        return ThemeSettings(**theme_colors_to_mapping(theme)), source, False
    return _default_theme_settings(), THEME_SOURCE_COMPAT_DEFAULT, True


def character_theme_to_mapping(
    settings: ThemeSettings | None,
    *,
    source: CharacterThemeSource = THEME_SOURCE_PACKAGE,
) -> dict[str, object]:
    from app.ui.theme import theme_colors_to_mapping

    settings = settings or _default_theme_settings()
    data = theme_colors_to_mapping(settings)
    data["source"] = _theme_source_from_text(source)
    return data


def save_character_theme(
    profile: CharacterProfile,
    settings: ThemeSettings,
    *,
    source: CharacterThemeSource = THEME_SOURCE_PACKAGE,
) -> None:
    manifest_path = profile.package_dir / "character.json"
    try:
        raw_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CharacterConfigError(f"角色清单无法读取：{manifest_path}") from exc
    if not isinstance(raw_data, dict):
        raise CharacterConfigError(f"角色清单必须是 JSON 对象：{manifest_path}")
    _write_character_theme_manifest(manifest_path, raw_data, settings, source=source)


def _load_expression_portraits(package_dir: Path, portrait_data: dict[str, Any]) -> dict[str, Path]:
    expressions = portrait_data.get("expressions", {})
    if expressions is None:
        return {}
    if not isinstance(expressions, dict):
        raise CharacterConfigError("portrait.expressions 必须是对象。")

    result: dict[str, Path] = {}
    for tone, path_text in expressions.items():
        if not isinstance(tone, str) or not isinstance(path_text, str):
            raise CharacterConfigError("portrait.expressions 的键和值都必须是字符串。")
        result[tone.strip()] = _resolve_required_file(package_dir, path_text, f"{tone} 表情立绘")
    return {tone: path for tone, path in result.items() if tone}


def _load_reply_tones(reply_data: Any) -> list[str]:
    if not isinstance(reply_data, dict):
        return [*DEFAULT_TONES]
    raw_tones = reply_data.get("tones")
    if not isinstance(raw_tones, list):
        return [*DEFAULT_TONES]
    tones = [tone.strip() for tone in raw_tones if isinstance(tone, str) and tone.strip()]
    return tones or [*DEFAULT_TONES]


def _load_renderer_config(raw_data: dict[str, Any]) -> dict[str, Any] | None:
    """读取角色清单的 renderer 段（原样保留）。

    本函数只做轻量校验（必须是对象），不解析模型/动作路径——那部分依赖具体
    渲染插件，由插件相对角色目录解析，避免在
    角色加载期因模型文件尚未就位而报错。
    """
    cfg = raw_data.get("renderer")
    return cfg if isinstance(cfg, dict) else None


def _load_voice(package_dir: Path, voice_data: Any, manifest_path: Path) -> CharacterVoice | None:
    if voice_data is None:
        return None
    if not isinstance(voice_data, dict):
        raise CharacterConfigError(f"voice 必须是对象：{manifest_path}")

    gpt_model_path = _resolve_optional_file(package_dir, _optional_text(voice_data, "gpt_model", ""))
    sovits_model_path = _resolve_optional_file(package_dir, _optional_text(voice_data, "sovits_model", ""))
    tone_ref_path = _resolve_required_file(
        package_dir,
        _required_text(voice_data, "tone_refs", manifest_path),
        "语气参考表",
    )

    return CharacterVoice(
        gpt_model_path=gpt_model_path,
        sovits_model_path=sovits_model_path,
        tone_ref_path=tone_ref_path,
        ref_lang=_optional_text(voice_data, "ref_lang", "ja"),
        text_lang=_optional_text(voice_data, "text_lang", "ja"),
    )


def _theme_source_from_text(value: object) -> CharacterThemeSource:
    return (
        THEME_SOURCE_COMPAT_DEFAULT
        if str(value or "").strip() == THEME_SOURCE_COMPAT_DEFAULT
        else THEME_SOURCE_PACKAGE
    )


def _default_theme_settings() -> ThemeSettings:
    from app.ui.theme import DEFAULT_THEME_SETTINGS

    return DEFAULT_THEME_SETTINGS


def _write_character_theme_manifest(
    manifest_path: Path,
    raw_data: dict[str, Any],
    settings: ThemeSettings,
    *,
    source: CharacterThemeSource,
) -> None:
    raw_data["theme"] = character_theme_to_mapping(settings, source=source)
    try:
        manifest_path.write_text(
            json.dumps(raw_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        raise CharacterConfigError(f"角色主题写回失败：{manifest_path}") from exc


def _required_dict(data: dict[str, Any], key: str, manifest_path: Path) -> dict[str, Any]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise CharacterConfigError(f"角色清单缺少对象字段 {key}：{manifest_path}")
    return value


def _required_text(data: dict[str, Any], key: str, manifest_path: Path) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CharacterConfigError(f"角色清单缺少文本字段 {key}：{manifest_path}")
    return value.strip()


def _optional_text(data: dict[str, Any], key: str, default: str) -> str:
    value = data.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return default


def _resolve_required_file(package_dir: Path, path_text: str, label: str) -> Path:
    path = _resolve_package_path(package_dir, path_text, require_exists=True)
    if not path.is_file():
        raise CharacterConfigError(f"{label}不存在：{path}")
    return path


def _resolve_optional_file(package_dir: Path, path_text: str) -> Path | None:
    if not path_text.strip():
        return None
    path = _resolve_package_path(package_dir, path_text, require_exists=True)
    if not path.is_file():
        raise CharacterConfigError(f"角色资源不存在：{path}")
    return path


def _resolve_package_path(package_dir: Path, path_text: str, *, require_exists: bool = False) -> Path:
    raw = path_text.strip().strip('"').strip("'")
    path = Path(raw)
    if not raw or path.is_absolute() or raw.startswith(("\\\\", "//")):
        raise CharacterConfigError(f"角色资源必须使用包内相对路径：{path_text!r}")
    package_root = package_dir.resolve(strict=True)
    try:
        resolved = (package_dir / path).resolve(strict=False)
        resolved.relative_to(package_root)
    except (OSError, ValueError) as exc:
        raise CharacterConfigError(f"角色资源路径逃逸角色包：{path_text!r}") from exc
    return resolved


_INVALID_CHARACTER_ID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _validate_character_id(character_id: str, manifest_path: Path) -> str:
    if character_id in {".", ".."} or _INVALID_CHARACTER_ID.search(character_id):
        raise CharacterConfigError(f"角色 id 包含不安全字符：{character_id!r}（{manifest_path}）")
    if sanitize_file_stem(character_id) != character_id:
        raise CharacterConfigError(f"角色 id 不是跨平台安全标识符：{character_id!r}（{manifest_path}）")
    return character_id


def _append_desktop_context(content: str) -> str:
    return with_desktop_pet_context(content)
