"""Workspace —— Studio 的工作区管理。

工作区是一个标准角色包根目录（其下含 characters/<id>/），所有编辑都落在工作区，
绝不直接读写主项目的 characters/ 生产目录。

复用主项目能力：
- import_character_archive：导入 .char 落地工作区；
- _load_profile：单包加载与校验（不受工作区内其他包影响）；
- export_character_archive：从 CharacterProfile 生成 .char。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from app.config.character_archive import export_character_archive, import_character_archive
from app.config.character_loader import CharacterProfile, _load_profile

from tools.studio.character_doc import CARD_FILENAME, CharacterDoc


class WorkspaceError(RuntimeError):
    """工作区操作失败。"""


def _validate_package_local_paths(package_dir: Path) -> None:
    manifest_path = package_dir / "character.json"
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkspaceError(f"角色清单无法读取：{manifest_path}") from exc
    if not isinstance(raw, dict):
        raise WorkspaceError(f"角色清单必须是 JSON 对象：{manifest_path}")

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
        raise WorkspaceError(f"{label}不能使用绝对路径：{value}")
    try:
        (package_dir / path).resolve().relative_to(package_dir.resolve())
    except ValueError as exc:
        raise WorkspaceError(f"{label}不能指向角色包外：{value}") from exc


class Workspace:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.characters_dir = self.root / "characters"
        self.characters_dir.mkdir(parents=True, exist_ok=True)

    def package_dir(self, char_id: str) -> Path:
        return self.characters_dir / char_id

    # ---- 创建 / 打开 ------------------------------------------------------

    def new_character(self, char_id: str, *, overwrite: bool = True) -> tuple[Path, CharacterDoc]:
        """新建空白角色包骨架。character.json 在首次保存时才写出。"""
        pkg = self._prepare_empty_dir(char_id, overwrite=overwrite)
        (pkg / "portraits").mkdir(exist_ok=True)
        (pkg / CARD_FILENAME).write_text("", encoding="utf-8")
        return pkg, CharacterDoc(id=char_id, display_name=char_id)

    def open_directory(self, src_dir: Path, *, overwrite: bool = True) -> tuple[Path, CharacterDoc]:
        """把现有角色包目录复制到工作区后打开（不改动源目录）。"""
        src_dir = Path(src_dir)
        if not (src_dir / "character.json").exists():
            raise WorkspaceError(f"目录不是角色包（缺 character.json）：{src_dir}")
        pkg = self._prepare_empty_dir(src_dir.name, overwrite=overwrite)
        shutil.copytree(src_dir, pkg, dirs_exist_ok=True)
        _validate_package_local_paths(pkg)
        return pkg, CharacterDoc.from_package_dir(pkg)

    def open_archive(self, archive_path: Path) -> tuple[Path, CharacterDoc]:
        """导入 .char 到工作区并打开。"""
        result = import_character_archive(Path(archive_path), base_dir=self.root)
        return result.package_dir, CharacterDoc.from_package_dir(result.package_dir)

    # ---- 保存 / 校验 / 导出 ----------------------------------------------

    def save(self, doc: CharacterDoc, package_dir: Path) -> None:
        """写出 card.md 与 character.json（不做存在性校验，允许保存草稿）。"""
        package_dir = Path(package_dir)
        package_dir.mkdir(parents=True, exist_ok=True)
        (package_dir / CARD_FILENAME).write_text(doc.card_text, encoding="utf-8")
        (package_dir / "character.json").write_text(doc.manifest_json(), encoding="utf-8")

    def validate(self, package_dir: Path) -> CharacterProfile:
        """用主项目的单包加载器校验，返回 CharacterProfile；失败抛 CharacterConfigError。"""
        package_dir = Path(package_dir)
        _validate_package_local_paths(package_dir)
        return _load_profile(package_dir / "character.json")

    def export(
        self,
        doc: CharacterDoc,
        package_dir: Path,
        output_path: Path,
        *,
        include_voice: bool = True,
    ) -> CharacterProfile:
        """保存 → 校验 → 导出 .char。返回校验通过的 profile。"""
        self.save(doc, package_dir)
        profile = self.validate(package_dir)
        export_character_archive(profile, Path(output_path), include_voice=include_voice)
        return profile

    # ---- 内部 -------------------------------------------------------------

    def _prepare_empty_dir(self, char_id: str, *, overwrite: bool = True) -> Path:
        pkg = self.package_dir(char_id)
        if pkg.exists():
            if not overwrite:
                raise WorkspaceError(f"工作区已存在角色草稿：{pkg}")
            shutil.rmtree(pkg)
        pkg.mkdir(parents=True, exist_ok=True)
        return pkg
