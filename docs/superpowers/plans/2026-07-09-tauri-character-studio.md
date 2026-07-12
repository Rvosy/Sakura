# Tauri Character Studio Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在设置页的角色区域打开一个与 `tools/settings-tauri` 视觉语言一致的 Tauri 角色工作室，支持列出现有本地角色、编辑核心字段、新建角色、保存回 `characters/`，并且保存后不切换当前角色。

**Architecture:** 新建独立的 `tools/studio-tauri` Tauri 应用，复用设置页的 JSONL `host_call` 协议和 `nav-card` / `detail-card` / `page-head` / `settings-group` 组件语言。Python 侧增加无 Qt 控件依赖的角色工作室服务，所有编辑先落在 `runtime/character-studio/workspace/characters/<id>/` 草稿，再由“保存”写回本地 `characters/<id>/`。设置页只负责通过 RPC 唤起工作室，角色保存不会写 `config/characters.yaml`，也不会自动切换当前角色。

**Tech Stack:** Python 3 + PySide6 `QProcess`/signals, Tauri 2 + Rust JSONL stdout/stdin bridge, vanilla HTML/CSS/JavaScript, pytest, Cargo tests.

---

## Spec Decisions

- 用户选择“第一种”落地路径：新增独立 Tauri 角色工作室，不改造旧 PySide6 Studio 的界面作为主要入口。
- 设置页角色区域新增“编辑当前角色”和“打开角色工作室”入口。
- 工作室首页列出当前 `characters/` 下所有角色，支持打开编辑，也支持新建角色。
- 编辑现有角色时，先复制到草稿工作区；点击“保存”后写回本地 `characters/<id>/`。
- 保存后不切换当前角色，不调用当前角色配置保存逻辑。
- UI 文案使用“保存”，不使用“发布”。
- 第一版覆盖核心编辑：基础信息、人设卡、开场白、回复语气、立绘路径/导入、主题色预览、导出 `.char`。
- 第一版不做语音模型编辑；现有角色的 `voice` 字段和资源在保存时保留，UI 只展示只读状态。

## File Structure

### Create

- `app/config/character_studio.py`
  - 角色工作室的无 UI 后端服务。
  - 定义 `CharacterStudioDoc` / `VoiceDraft` / `CharacterStudioService`。
  - 负责角色列表、打开到草稿、新建草稿、导入立绘、保存回 `characters/`、导出 `.char`。

- `app/ui/tauri_studio.py`
  - 独立 Tauri Studio 的 Python 进程桥。
  - 定义 `SAKURA_TAURI_STUDIO_BIN` 环境变量、协议 marker、`resolve_tauri_studio_binary`、`build_tauri_studio_request`、`dispatch_tauri_studio_rpc`、`TauriStudioProcess`。

- `tools/studio-tauri/frontend/index.html`
  - 工作室 HTML 骨架，使用与设置页一致的导航卡、详情卡、设置组、底栏。

- `tools/studio-tauri/frontend/studio.js`
  - 工作室前端状态、页面切换、角色列表、编辑表单、保存、导出、立绘导入。

- `tools/studio-tauri/frontend/styles.css`
  - 从 `tools/settings-tauri/frontend/styles.css` 提取主题变量、布局、动效，再添加工作室列表/编辑器样式。

- `tools/studio-tauri/src-tauri/Cargo.toml`
  - Tauri Studio Rust crate，包名 `sakura-studio`。

- `tools/studio-tauri/src-tauri/build.rs`
  - 标准 Tauri build script。

- `tools/studio-tauri/src-tauri/src/main.rs`
  - 调用 `sakura_studio_lib::run()`。

- `tools/studio-tauri/src-tauri/src/lib.rs`
  - JSONL request/RPC bridge，形态对齐 `tools/settings-tauri/src-tauri/src/lib.rs`，marker 使用 `@@SAKURA_STUDIO_*@@`。

- `tools/studio-tauri/src-tauri/tauri.conf.json`
  - 独立窗口配置，标题 `Sakura 角色工作室`，尺寸 `1120x760`。

- `tools/studio-tauri/src-tauri/capabilities/default.json`
  - 允许 `core:default`、窗口关闭、打开/保存文件对话框。

- `tests/unit/test_character_studio.py`
  - 后端服务单元测试。

- `tests/unit/test_tauri_studio.py`
  - Python Tauri Studio 桥测试。

### Modify

- `tools/studio/character_doc.py`
  - 改成从 `app.config.character_studio` 重新导出 `CARD_FILENAME`、`DEFAULT_TONE_REFS`、`VoiceDraft`、`CharacterStudioDoc as CharacterDoc`，保持旧 PySide6 Studio 测试和导入路径兼容。

- `tools/studio/workspace.py`
  - 继续保留旧 PySide6 工作区 API，只把 `CharacterDoc` / `CARD_FILENAME` 的来源指向 `app.config.character_studio`。

- `app/ui/tauri_settings.py`
  - `TauriSettingsProcess` 增加 `studio_launcher` 回调。
  - 支持 settings 前端调用 `hostCall("studio.launch", { character_id })`。

- `app/ui/pet_window.py`
  - 管理 `self.tauri_studio_process`。
  - 给 Tauri 设置页传入工作室启动回调。
  - 重复打开时聚焦已有工作室窗口。
  - 应用关闭时关闭工作室进程。

- `tools/settings-tauri/frontend/index.html`
  - 在角色页“角色包”下增加“角色工作室”设置组。

- `tools/settings-tauri/frontend/settings.js`
  - 增加 `characterStudioOpenButton` / `characterStudioCurrentButton` 字段与点击事件。
  - 点击后调用 `hostCall("studio.launch", { character_id })`，不标记设置页 dirty。

- `tools/settings-tauri/frontend/styles.css`
  - 复用 `.archive-controls` 的按钮布局；只在需要时补充 `.studio-controls`。

- `tests/ui/test_pet_window.py`
  - 增加设置页工作室 RPC、PetWindow 启动/聚焦/关闭流程测试。
  - 增加设置页前端静态断言。

- `tests/ui/test_studio.py`
  - 仅在共享 dataclass 影响旧导入时补充兼容断言。

## Task 0: Execution Setup

**Files:**
- Read: `AGENTS.md`
- Branch: `feat/tauri-character-studio`

- [ ] **Step 1: Create an isolated feature branch from dev**

Run:

```powershell
git fetch origin
git worktree add ..\Sakura-tauri-character-studio origin/dev -b feat/tauri-character-studio
Set-Location ..\Sakura-tauri-character-studio
```

Expected:

```text
Preparing worktree (new branch 'feat/tauri-character-studio')
HEAD is now at <dev commit>
```

- [ ] **Step 2: Confirm the execution worktree is clean**

Run:

```powershell
git status --short --branch
```

Expected:

```text
## feat/tauri-character-studio
```

No modified or untracked files should be listed in the execution worktree before implementation starts.

## Task 1: Character Studio Backend Tests

**Files:**
- Create: `tests/unit/test_character_studio.py`
- Read: `app/config/character_loader.py`
- Read: `app/config/character_archive.py`
- Read: `tools/studio/character_doc.py`
- Read: `tools/studio/workspace.py`

- [ ] **Step 1: Write failing tests for list, draft open, create, portrait import, save, and export**

Create `tests/unit/test_character_studio.py` with:

```python
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from app.config.character_loader import CharacterRegistry
from app.ui.theme import ThemeSettings


def _runtime_root(name: str) -> Path:
    root = Path("runtime") / "tests" / "character_studio" / name
    if root.exists():
        import shutil

        shutil.rmtree(root)
    root.mkdir(parents=True)
    return root


def _write_character(root: Path, character_id: str = "sakura", display_name: str = "Sakura") -> Path:
    package_dir = root / "characters" / character_id
    (package_dir / "portraits").mkdir(parents=True)
    (package_dir / "voice" / "refs").mkdir(parents=True)
    (package_dir / "card.md").write_text("old card", encoding="utf-8")
    (package_dir / "portraits" / "default.png").write_bytes(b"png")
    (package_dir / "voice" / "refs" / "ref.txt").write_text("", encoding="utf-8")
    (package_dir / "character.json").write_text(
        json.dumps(
            {
                "id": character_id,
                "display_name": display_name,
                "initial_message": "hello",
                "card": "card.md",
                "portrait": {
                    "default": "portraits/default.png",
                    "expressions": {"开心": "portraits/default.png"},
                },
                "reply": {"tones": ["温柔"]},
                "theme": {
                    "source": "package",
                    "primary_color": "#112233",
                    "accent_color": "#445566",
                },
                "voice": {"tone_refs": "voice/refs/ref.txt", "ref_lang": "ja", "text_lang": "ja"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return package_dir


def test_character_studio_lists_characters_and_marks_current() -> None:
    from app.config.character_studio import CharacterStudioService

    root = _runtime_root("list")
    _write_character(root, "sakura", "Sakura")
    _write_character(root, "rin", "Rin")

    service = CharacterStudioService(root)
    items = service.list_characters(current_character_id="rin")

    assert [item["id"] for item in items] == ["rin", "sakura"]
    assert items[0]["is_current"] is True
    assert items[0]["display_name"] == "Rin"
    assert items[0]["has_voice"] is True
    assert items[0]["source"] == "installed"


def test_character_studio_open_uses_draft_without_touching_source() -> None:
    from app.config.character_studio import CharacterStudioService

    root = _runtime_root("draft_open")
    source = _write_character(root)

    service = CharacterStudioService(root)
    opened = service.open_character("sakura")
    draft_dir = Path(opened["package_dir"])
    assert draft_dir != source
    assert draft_dir.exists()
    assert opened["doc"]["id"] == "sakura"
    assert opened["doc"]["card_text"] == "old card"

    opened["doc"]["card_text"] = "draft only"
    service.save_draft(opened["doc"], draft_dir)

    assert (source / "card.md").read_text(encoding="utf-8") == "old card"
    assert (draft_dir / "card.md").read_text(encoding="utf-8") == "draft only"


def test_character_studio_create_import_portrait_and_save_new_character() -> None:
    from app.config.character_studio import CharacterStudioService

    root = _runtime_root("new_character")
    service = CharacterStudioService(root)
    portrait_source = root / "source.png"
    portrait_source.write_bytes(b"new portrait")

    created = service.create_character({"id": "new_role", "display_name": "新角色"})
    draft_dir = Path(created["package_dir"])
    portrait = service.import_portrait(draft_dir, portrait_source, label="default")
    doc = created["doc"]
    doc["card_text"] = "system prompt"
    doc["initial_message"] = "初次见面"
    doc["default_portrait"] = portrait["relative_path"]
    doc["reply_tones"] = ["沉稳", "轻快"]
    doc["theme"]["primary_color"] = "#223344"
    doc["theme"]["accent_color"] = "#556677"

    saved = service.save_character(doc, draft_dir, current_character_id="sakura")

    assert saved["saved_character_id"] == "new_role"
    assert saved["current_character_id"] == "sakura"
    profile = CharacterRegistry(root).get("new_role")
    assert profile.display_name == "新角色"
    assert profile.reply_tones == ["沉稳", "轻快"]
    assert profile.voice is None
    assert (profile.package_dir / "portraits" / "default.png").read_bytes() == b"new portrait"


def test_character_studio_save_existing_preserves_voice_and_exports_char() -> None:
    from app.config.character_studio import CharacterStudioService

    root = _runtime_root("save_existing")
    _write_character(root, "sakura", "Sakura")
    service = CharacterStudioService(root)
    opened = service.open_character("sakura")
    draft_dir = Path(opened["package_dir"])
    doc = opened["doc"]
    doc["display_name"] = "Sakura Edited"
    doc["card_text"] = "new card"
    doc["theme"]["primary_color"] = "#abcdef"

    saved = service.save_character(doc, draft_dir, current_character_id="sakura")

    profile = CharacterRegistry(root).get("sakura")
    assert saved["current_character_id"] == "sakura"
    assert profile.display_name == "Sakura Edited"
    assert profile.voice is not None
    assert (profile.package_dir / "card.md").read_text(encoding="utf-8") == "new card"
    manifest = json.loads((profile.package_dir / "character.json").read_text(encoding="utf-8"))
    assert manifest["theme"]["source"] == "package"
    assert manifest["theme"]["primary_color"] == "#abcdef"

    archive_path = root / "sakura.card.char"
    result = service.export_archive(draft_dir, archive_path, include_voice=False)
    assert result["output_path"] == str(archive_path)
    with zipfile.ZipFile(archive_path) as zf:
        manifest = json.loads(zf.read("manifest.json"))
        assert manifest["character"]["id"] == "sakura"
        assert "voice" not in manifest["character"]


def test_character_studio_rejects_unsafe_ids_and_paths() -> None:
    from app.config.character_studio import CharacterStudioService

    root = _runtime_root("validation")
    service = CharacterStudioService(root)

    with pytest.raises(ValueError, match="角色 id"):
        service.create_character({"id": "../bad", "display_name": "Bad"})

    with pytest.raises(ValueError, match="角色 id"):
        service.open_character("../bad")

    outside = root.parent / "outside.png"
    outside.write_bytes(b"png")
    created = service.create_character({"id": "safe", "display_name": "Safe"})
    draft_dir = Path(created["package_dir"])
    with pytest.raises(ValueError, match="文件扩展名"):
        service.import_portrait(draft_dir, root / "bad.txt", label="default")

    assert outside.exists()
```

- [ ] **Step 2: Run backend tests to verify they fail**

Run:

```powershell
.\runtime\python.exe -m pytest tests/unit/test_character_studio.py -q
```

Expected:

```text
E   ModuleNotFoundError: No module named 'app.config.character_studio'
```

or:

```text
FAILED tests/unit/test_character_studio.py::test_character_studio_lists_characters_and_marks_current
```

## Task 2: Character Studio Backend Implementation

**Files:**
- Create: `app/config/character_studio.py`
- Modify: `tools/studio/character_doc.py`
- Modify: `tools/studio/workspace.py`
- Test: `tests/unit/test_character_studio.py`
- Test: `tests/ui/test_studio.py`

- [ ] **Step 1: Implement shared doc model and service**

Create `app/config/character_studio.py` with these public names and behavior:

```python
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
    CharacterConfigError,
    CharacterProfile,
    CharacterRegistry,
    THEME_SOURCE_PACKAGE,
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
    tone_refs: str = DEFAULT_TONE_REFS
    gpt_model: str | None = None
    sovits_model: str | None = None
    ref_lang: str = "ja"
    text_lang: str = "ja"


@dataclass
class CharacterStudioDoc:
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
            "theme": character_theme_to_mapping(self.theme.normalized(), source=THEME_SOURCE_PACKAGE),
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
            "voice": None if self.voice is None else {
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
        theme = theme_from_mapping(payload.get("theme")).normalized()
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
        expressions = payload.get("expressions") if isinstance(payload.get("expressions"), dict) else {}
        reply_tones = payload.get("reply_tones") if isinstance(payload.get("reply_tones"), list) else []
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
            theme=theme,
            voice=voice,
        )

    @classmethod
    def from_package_dir(cls, package_dir: Path) -> "CharacterStudioDoc":
        manifest_path = Path(package_dir) / "character.json"
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"character.json 必须是 JSON 对象：{manifest_path}")
        portrait = raw.get("portrait") if isinstance(raw.get("portrait"), dict) else {}
        expressions = portrait.get("expressions") if isinstance(portrait.get("expressions"), dict) else {}
        reply = raw.get("reply") if isinstance(raw.get("reply"), dict) else {}
        tones = reply.get("tones") if isinstance(reply.get("tones"), list) else []
        card_name = str(raw.get("card") or CARD_FILENAME)
        card_path = Path(package_dir) / card_name
        card_text = card_path.read_text(encoding="utf-8") if card_path.exists() else ""
        voice = None
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
            expressions={str(k): str(v) for k, v in expressions.items() if isinstance(k, str) and isinstance(v, str)},
            reply_tones=[str(t) for t in tones if isinstance(t, str) and t.strip()],
            theme=theme_from_mapping(raw.get("theme")).normalized(),
            voice=voice,
        )
```

Continue in the same file with the service:

```python
class CharacterStudioService:
    def __init__(self, base_dir: Path, workspace_root: Path | None = None) -> None:
        self.base_dir = Path(base_dir)
        self.characters_dir = self.base_dir / "characters"
        self.workspace_root = Path(workspace_root) if workspace_root else (
            self.base_dir / "runtime" / "character-studio" / "workspace"
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
        items = [self._summary_from_profile(profile, current_character_id) for profile in profiles]
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
            staging_dir.replace(target_dir)
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
        return {
            "saved_character_id": profile.id,
            "current_character_id": str(current_character_id or ""),
            "characters": self.list_characters(current_character_id=str(current_character_id or "")),
            "doc": CharacterStudioDoc.from_package_dir(target_dir).to_payload(),
            "package_dir": str(draft_dir),
            "message": f"已保存角色「{registry.get(profile.id).display_name}」。",
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
        return {"relative_path": target.relative_to(package_dir).as_posix(), "path": str(target)}

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
        return {"output_path": str(output), "message": f"角色包已导出到：{output}"}

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
        return {
            "id": profile.id,
            "display_name": profile.display_name,
            "package_dir": str(profile.package_dir),
            "is_current": profile.id == current_character_id,
            "has_voice": profile.voice is not None,
            "source": "installed",
            "theme": theme_to_mapping((profile.theme_settings or DEFAULT_THEME_SETTINGS).normalized()),
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
        shutil.copytree(target_dir, backup_dir)
        return backup_dir
```

Add the helper functions at the end of the same file:

```python
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
```

- [ ] **Step 2: Keep old Studio import paths compatible**

Replace `tools/studio/character_doc.py` content with:

```python
from __future__ import annotations

from app.config.character_studio import (
    CARD_FILENAME,
    DEFAULT_TONE_REFS,
    CharacterStudioDoc as CharacterDoc,
    VoiceDraft,
)

__all__ = ["CARD_FILENAME", "DEFAULT_TONE_REFS", "CharacterDoc", "VoiceDraft"]
```

In `tools/studio/workspace.py`, change the import:

```python
from tools.studio.character_doc import CARD_FILENAME, CharacterDoc
```

to:

```python
from app.config.character_studio import CARD_FILENAME, CharacterStudioDoc as CharacterDoc
```

- [ ] **Step 3: Run backend tests**

Run:

```powershell
.\runtime\python.exe -m pytest tests/unit/test_character_studio.py -q
```

Expected:

```text
5 passed
```

- [ ] **Step 4: Run old Studio compatibility tests**

Run:

```powershell
.\runtime\python.exe -m pytest tests/ui/test_studio.py -q
```

Expected:

```text
passed
```

- [ ] **Step 5: Commit backend service**

Run:

```powershell
git add app/config/character_studio.py tools/studio/character_doc.py tools/studio/workspace.py tests/unit/test_character_studio.py
git commit -m "feat: 增加角色工作室后端服务"
```

Expected:

```text
[feat/tauri-character-studio <hash>] feat: 增加角色工作室后端服务
```

## Task 3: Python Tauri Studio Bridge Tests

**Files:**
- Create: `tests/unit/test_tauri_studio.py`
- Modify: `tests/ui/test_pet_window.py`
- Read: `app/ui/tauri_settings.py`
- Read: `app/ui/pet_window.py`

- [ ] **Step 1: Write failing unit tests for the Studio process bridge**

Create `tests/unit/test_tauri_studio.py` with:

```python
from __future__ import annotations

import json
from pathlib import Path

import pytest


def _runtime_root(name: str) -> Path:
    root = Path("runtime") / "tests" / "tauri_studio" / name
    if root.exists():
        import shutil

        shutil.rmtree(root)
    root.mkdir(parents=True)
    return root


def test_resolve_tauri_studio_binary_uses_env_and_platform(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.ui.tauri_studio as tauri_studio

    root = _runtime_root("binary")
    custom = root / "custom-studio.exe"
    custom.write_text("bin", encoding="utf-8")
    assert tauri_studio.resolve_tauri_studio_binary(
        root,
        environ={tauri_studio.TAURI_STUDIO_BIN_ENV: str(custom)},
    ) == custom

    release = root / "tools" / "studio-tauri" / "src-tauri" / "target" / "release"
    release.mkdir(parents=True)
    win_bin = release / "sakura-studio.exe"
    unix_bin = release / "sakura-studio"
    win_bin.write_text("win", encoding="utf-8")
    unix_bin.write_text("unix", encoding="utf-8")

    monkeypatch.setattr(tauri_studio.sys, "platform", "win32")
    assert tauri_studio.resolve_tauri_studio_binary(root, environ={}) == win_bin
    monkeypatch.setattr(tauri_studio.sys, "platform", "darwin")
    assert tauri_studio.resolve_tauri_studio_binary(root, environ={}) == unix_bin


def test_build_tauri_studio_request_contains_characters_and_nonce() -> None:
    from app.config.character_studio import CharacterStudioService
    from app.ui.tauri_studio import build_tauri_studio_request

    root = _runtime_root("request")
    package_dir = root / "characters" / "sakura"
    package_dir.mkdir(parents=True)
    (package_dir / "card.md").write_text("card", encoding="utf-8")
    (package_dir / "portrait.png").write_bytes(b"png")
    (package_dir / "character.json").write_text(
        json.dumps(
            {
                "id": "sakura",
                "display_name": "Sakura",
                "card": "card.md",
                "portrait": {"default": "portrait.png"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    request = build_tauri_studio_request(root, initial_character_id="sakura", nonce="nonce")

    assert request["version"] == 1
    assert request["nonce"] == "nonce"
    assert request["initial_character_id"] == "sakura"
    assert request["characters"][0]["id"] == "sakura"
    assert request["theme_fields"]
    assert CharacterStudioService(root).list_characters(current_character_id="sakura")[0]["is_current"] is True


def test_dispatch_tauri_studio_rpc_routes_core_methods(tmp_path: Path) -> None:
    from app.ui.tauri_studio import dispatch_tauri_studio_rpc

    root = tmp_path
    source = root / "source.png"
    source.write_bytes(b"png")

    created = dispatch_tauri_studio_rpc(
        root,
        "studio.create_character",
        {"doc": {"id": "demo", "display_name": "Demo"}},
    )
    draft_dir = created["package_dir"]
    portrait = dispatch_tauri_studio_rpc(
        root,
        "studio.import_portrait",
        {"package_dir": draft_dir, "path": str(source), "label": "default"},
    )
    doc = created["doc"]
    doc["card_text"] = "card"
    doc["default_portrait"] = portrait["relative_path"]
    saved = dispatch_tauri_studio_rpc(
        root,
        "studio.save_character",
        {"package_dir": draft_dir, "doc": doc, "current_character_id": "sakura"},
    )

    assert saved["saved_character_id"] == "demo"
    assert saved["current_character_id"] == "sakura"
    assert (root / "characters" / "demo" / "character.json").exists()


def test_tauri_studio_process_writes_rpc_response_line() -> None:
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")
    qtwidgets.QApplication.instance() or qtwidgets.QApplication([])

    from app.ui.tauri_studio import (
        TAURI_STUDIO_RPC_MARKER,
        TAURI_STUDIO_RPC_RESULT_MARKER,
        TauriStudioProcess,
    )

    class FakeQProcess:
        def __init__(self, chunk: bytes) -> None:
            self._chunk = chunk
            self.writes: list[bytes] = []

        def readAllStandardOutput(self) -> bytes:
            chunk, self._chunk = self._chunk, b""
            return chunk

        def write(self, data: bytes) -> int:
            self.writes.append(bytes(data))
            return len(data)

    root = _runtime_root("process_rpc")
    request = {"id": "rpc-1", "method": "studio.list_characters", "params": {}}
    fake = FakeQProcess(
        (TAURI_STUDIO_RPC_MARKER + json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8")
    )
    process = TauriStudioProcess(root, initial_character_id="")
    process._process = fake

    process._handle_stdout()

    line = b"".join(fake.writes).decode("utf-8").strip()
    assert line.startswith(TAURI_STUDIO_RPC_RESULT_MARKER)
    payload = json.loads(line[len(TAURI_STUDIO_RPC_RESULT_MARKER):])
    assert payload["id"] == "rpc-1"
    assert payload["ok"] is True
    assert payload["result"]["characters"] == []
```

- [ ] **Step 2: Add failing settings/PetWindow tests for launching Studio**

Append these tests near existing Tauri settings tests in `tests/ui/test_pet_window.py`:

```python
def test_tauri_settings_dispatches_studio_launch_callback() -> None:
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")
    qtwidgets.QApplication.instance() or qtwidgets.QApplication([])

    from app.ui.tauri_settings import TauriSettingsProcess

    calls: list[str] = []
    process = TauriSettingsProcess(
        base_dir=Path("."),
        settings=ScreenAwarenessSettings(),
        studio_launcher=lambda character_id: calls.append(character_id or "") or True,
    )

    result = process._dispatch_rpc("studio.launch", {"character_id": "sakura"})

    assert calls == ["sakura"]
    assert result["message"] == "角色工作室已打开。"


def test_tauri_settings_dispatches_studio_launch_failure() -> None:
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")
    qtwidgets.QApplication.instance() or qtwidgets.QApplication([])

    from app.ui.tauri_settings import TauriSettingsProcess

    process = TauriSettingsProcess(
        base_dir=Path("."),
        settings=ScreenAwarenessSettings(),
        studio_launcher=lambda _character_id: False,
    )

    with pytest.raises(ValueError, match="角色工作室"):
        process._dispatch_rpc("studio.launch", {"character_id": "sakura"})


def test_tauri_settings_frontend_has_studio_buttons_without_publish_wording() -> None:
    index = Path("tools/settings-tauri/frontend/index.html").read_text(encoding="utf-8")
    source = Path("tools/settings-tauri/frontend/settings.js").read_text(encoding="utf-8")

    assert "characterStudioCurrentButton" in index
    assert "characterStudioOpenButton" in index
    assert "hostCall(\"studio.launch\"" in source
    assert "发布" not in index
    assert "发布" not in source
```

- [ ] **Step 3: Run bridge tests to verify they fail**

Run:

```powershell
.\runtime\python.exe -m pytest tests/unit/test_tauri_studio.py tests/ui/test_pet_window.py::test_tauri_settings_dispatches_studio_launch_callback tests/ui/test_pet_window.py::test_tauri_settings_dispatches_studio_launch_failure tests/ui/test_pet_window.py::test_tauri_settings_frontend_has_studio_buttons_without_publish_wording -q
```

Expected:

```text
E   ModuleNotFoundError: No module named 'app.ui.tauri_studio'
```

or frontend assertions fail because the buttons and `studio.launch` call do not exist yet.

## Task 4: Python Tauri Studio Bridge Implementation

**Files:**
- Create: `app/ui/tauri_studio.py`
- Modify: `app/ui/tauri_settings.py`
- Modify: `app/ui/pet_window.py`
- Test: `tests/unit/test_tauri_studio.py`
- Test: `tests/ui/test_pet_window.py`

- [ ] **Step 1: Implement `app/ui/tauri_studio.py`**

Create `app/ui/tauri_studio.py` with:

```python
from __future__ import annotations

import json
import os
import sys
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal

from app.config.character_studio import CharacterStudioService
from app.ui.theme import DEFAULT_THEME_SETTINGS, theme_to_mapping

TAURI_STUDIO_BIN_ENV = "SAKURA_TAURI_STUDIO_BIN"
TAURI_STUDIO_PROTOCOL_VERSION = 1
TAURI_STUDIO_RPC_MARKER = "@@SAKURA_STUDIO_RPC@@"
TAURI_STUDIO_RPC_RESULT_MARKER = "@@SAKURA_STUDIO_RPC_RESULT@@"


def resolve_tauri_studio_binary(base_dir: Path, environ: Mapping[str, str] | None = None) -> Path | None:
    env = environ or os.environ
    configured = env.get(TAURI_STUDIO_BIN_ENV)
    if configured:
        path = Path(configured)
        return path if path.is_file() else None
    root = Path(base_dir)
    binary_name = "sakura-studio.exe" if sys.platform == "win32" else "sakura-studio"
    candidates = (
        root / "tools" / "studio-tauri" / "src-tauri" / "target" / "release" / binary_name,
        root / "tools" / "studio-tauri" / "src-tauri" / "target" / "debug" / binary_name,
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def build_tauri_studio_request(
    base_dir: Path,
    *,
    initial_character_id: str = "",
    nonce: str | None = None,
) -> dict[str, Any]:
    service = CharacterStudioService(base_dir)
    return {
        "version": TAURI_STUDIO_PROTOCOL_VERSION,
        "nonce": nonce or uuid.uuid4().hex,
        "initial_character_id": str(initial_character_id or ""),
        "characters": service.list_characters(current_character_id=str(initial_character_id or "")),
        "theme": theme_to_mapping(DEFAULT_THEME_SETTINGS),
        "theme_fields": list(theme_to_mapping(DEFAULT_THEME_SETTINGS).keys()),
    }


def dispatch_tauri_studio_rpc(base_dir: Path, method: str, params: dict[str, Any]) -> dict[str, Any]:
    if not method.startswith("studio."):
        raise ValueError(f"未知 Tauri Studio RPC 方法：{method}")
    service = CharacterStudioService(base_dir)
    if method == "studio.list_characters":
        current_character_id = str(params.get("current_character_id") or "")
        return {"characters": service.list_characters(current_character_id=current_character_id)}
    if method == "studio.open_character":
        return service.open_character(_required_str(params, "character_id"))
    if method == "studio.create_character":
        doc = params.get("doc")
        if not isinstance(doc, dict):
            raise ValueError("studio.create_character 需要 doc 对象。")
        return service.create_character(doc)
    if method == "studio.save_draft":
        doc = params.get("doc")
        if not isinstance(doc, dict):
            raise ValueError("studio.save_draft 需要 doc 对象。")
        return service.save_draft(doc, _required_path(params, "package_dir"))
    if method == "studio.save_character":
        doc = params.get("doc")
        if not isinstance(doc, dict):
            raise ValueError("studio.save_character 需要 doc 对象。")
        return service.save_character(
            doc,
            _required_path(params, "package_dir"),
            current_character_id=str(params.get("current_character_id") or ""),
        )
    if method == "studio.import_portrait":
        return service.import_portrait(
            _required_path(params, "package_dir"),
            _required_path(params, "path"),
            label=str(params.get("label") or "default"),
        )
    if method == "studio.export_archive":
        return service.export_archive(
            _required_path(params, "package_dir"),
            _required_path(params, "path"),
            include_voice=bool(params.get("include_voice")),
        )
    raise ValueError(f"未知 Tauri Studio RPC 方法：{method}")


class TauriStudioProcess(QObject):
    closed = Signal()
    failed = Signal(str)

    def __init__(self, base_dir: Path, *, initial_character_id: str = "", parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.base_dir = Path(base_dir)
        self.initial_character_id = str(initial_character_id or "")
        self._process: QProcess | None = None
        self._request_payload = b""
        self._stdout_buffer = ""
        self._done = False

    def start(self) -> bool:
        binary = resolve_tauri_studio_binary(self.base_dir)
        if binary is None:
            return False
        request = build_tauri_studio_request(
            self.base_dir,
            initial_character_id=self.initial_character_id,
        )
        process = QProcess(self)
        process.setProgram(str(binary))
        process.setArguments([])
        process.setWorkingDirectory(str(self.base_dir))
        process.setProcessEnvironment(QProcessEnvironment.systemEnvironment())
        process.started.connect(self._send_request)
        process.finished.connect(self._handle_finished)
        process.errorOccurred.connect(self._handle_error)
        process.readyReadStandardOutput.connect(self._handle_stdout)
        self._process = process
        self._request_payload = json.dumps(request, ensure_ascii=False).encode("utf-8")
        process.start()
        return True

    def focus_window(self) -> bool:
        from app.ui.tauri_settings import _restore_windows_for_pid

        process = self._process
        if process is None:
            return False
        try:
            pid = int(process.processId())
        except (TypeError, ValueError):
            return False
        return pid > 0 and sys.platform == "win32" and _restore_windows_for_pid(pid)

    def shutdown(self, timeout_ms: int = 1000) -> None:
        self._done = True
        process = self._process
        if process is not None:
            try:
                process.closeWriteChannel()
            except RuntimeError:
                pass
            try:
                if process.state() != QProcess.ProcessState.NotRunning:
                    process.terminate()
                    if not process.waitForFinished(timeout_ms):
                        process.kill()
                        process.waitForFinished(timeout_ms)
            except RuntimeError:
                pass
        self._process = None

    def _send_request(self) -> None:
        process = self._process
        if process is None or self._done:
            return
        try:
            process.write(self._request_payload + b"\n")
        except RuntimeError as exc:
            self._done = True
            self.failed.emit(f"Tauri 角色工作室请求发送失败：{exc}")

    def _handle_stdout(self, *, flush: bool = False) -> None:
        process = self._process
        if process is None:
            return
        chunk = bytes(process.readAllStandardOutput()).decode("utf-8", errors="replace")
        if not chunk and not flush:
            return
        self._stdout_buffer += chunk
        *lines, self._stdout_buffer = self._stdout_buffer.split("\n")
        if flush and self._stdout_buffer:
            lines.append(self._stdout_buffer)
            self._stdout_buffer = ""
        for line in lines:
            stripped = line.strip()
            if stripped.startswith(TAURI_STUDIO_RPC_MARKER):
                self._handle_rpc_request(stripped[len(TAURI_STUDIO_RPC_MARKER):])

    def _handle_rpc_request(self, payload: str) -> None:
        try:
            request = json.loads(payload)
            if not isinstance(request, dict):
                raise ValueError("RPC 请求必须是对象。")
            request_id = request.get("id")
            method = request.get("method")
            params = request.get("params", {})
            if not isinstance(request_id, str) or not request_id:
                raise ValueError("RPC 请求缺少 id。")
            if not isinstance(method, str) or not method:
                raise ValueError("RPC 请求缺少 method。")
            if not isinstance(params, dict):
                raise ValueError("RPC params 必须是对象。")
            result = dispatch_tauri_studio_rpc(self.base_dir, method, params)
        except Exception as exc:
            self._send_rpc_response(str(request.get("id") or "") if isinstance(request, dict) else "", ok=False, error=str(exc))
            return
        self._send_rpc_response(request_id, ok=True, result=result)

    def _send_rpc_response(
        self,
        request_id: str,
        *,
        ok: bool,
        result: dict[str, Any] | None = None,
        error: str = "",
    ) -> None:
        process = self._process
        if process is None:
            return
        payload = {"id": request_id, "ok": ok, "result": result if ok else None, "error": "" if ok else error}
        line = TAURI_STUDIO_RPC_RESULT_MARKER + json.dumps(payload, ensure_ascii=False) + "\n"
        process.write(line.encode("utf-8"))

    def _handle_finished(self, _exit_code: int, _exit_status: QProcess.ExitStatus) -> None:
        self._handle_stdout(flush=True)
        self._done = True
        self._process = None
        self.closed.emit()

    def _handle_error(self, error: QProcess.ProcessError) -> None:
        self._done = True
        self.failed.emit(f"Tauri 角色工作室启动失败：{error.name}")


def _required_str(mapping: dict[str, Any], key: str) -> str:
    value = str(mapping.get(key) or "").strip()
    if not value:
        raise ValueError(f"缺少字段：{key}")
    return value


def _required_path(mapping: dict[str, Any], key: str) -> Path:
    return Path(_required_str(mapping, key))
```

- [ ] **Step 2: Add `studio.launch` RPC to settings process**

In `app/ui/tauri_settings.py`, import the callable type if it is not already imported:

```python
from collections.abc import Callable, Mapping
```

Add an optional parameter to `TauriSettingsProcess.__init__`:

```python
studio_launcher: Callable[[str | None], bool] | None = None,
```

Store it:

```python
self.studio_launcher = studio_launcher
```

In `_dispatch_rpc`, before plugin/memory/character routing, add:

```python
if method == "studio.launch":
    if self.studio_launcher is None:
        raise ValueError("角色工作室启动器不可用。")
    character_id = str(params.get("character_id") or "").strip() or None
    if not self.studio_launcher(character_id):
        raise ValueError("角色工作室未启动，请先构建 Tauri 角色工作室。")
    return {"message": "角色工作室已打开。"}
```

- [ ] **Step 3: Manage Studio process in PetWindow**

In `app/ui/pet_window.py`, import:

```python
from app.ui.tauri_studio import TauriStudioProcess, resolve_tauri_studio_binary
```

In `PetWindow.__init__`, add:

```python
self.tauri_studio_process: TauriStudioProcess | None = None
```

Add these methods to `PetWindow` near the Tauri settings helpers:

```python
def _open_tauri_studio_from_settings(self, character_id: str | None = None) -> bool:
    active_process = getattr(self, "tauri_studio_process", None)
    if active_process is not None:
        active_process.focus_window()
        return True
    if resolve_tauri_studio_binary(self.base_dir) is None:
        return False
    process = TauriStudioProcess(
        self.base_dir,
        initial_character_id=str(character_id or getattr(self.character_profile, "id", "") or ""),
        parent=self,
    )
    process.closed.connect(self._on_tauri_studio_closed)
    process.failed.connect(self._on_tauri_studio_failed)
    if not process.start():
        return False
    self.tauri_studio_process = process
    return True


def _on_tauri_studio_closed(self) -> None:
    self.tauri_studio_process = None
    try:
        self.character_registry = CharacterRegistry(self.base_dir)
    except Exception:
        pass


def _on_tauri_studio_failed(self, message: str) -> None:
    self.tauri_studio_process = None
    show_themed_critical(self, "角色工作室", message)


def _close_tauri_studio_process_for_shutdown(self) -> None:
    process = getattr(self, "tauri_studio_process", None)
    if process is not None:
        process.shutdown()
    self.tauri_studio_process = None
```

When constructing `TauriSettingsProcess` in `_try_show_tauri_settings`, pass:

```python
studio_launcher=self._open_tauri_studio_from_settings,
```

In the existing shutdown flow that calls `_close_tauri_settings_process_for_shutdown`, also call:

```python
close_tauri_studio = getattr(self, "_close_tauri_studio_process_for_shutdown", None)
if callable(close_tauri_studio):
    close_tauri_studio()
```

- [ ] **Step 4: Run bridge and PetWindow tests**

Run:

```powershell
.\runtime\python.exe -m pytest tests/unit/test_tauri_studio.py tests/ui/test_pet_window.py::test_tauri_settings_dispatches_studio_launch_callback tests/ui/test_pet_window.py::test_tauri_settings_dispatches_studio_launch_failure -q
```

Expected:

```text
passed
```

- [ ] **Step 5: Commit Python bridge**

Run:

```powershell
git add app/ui/tauri_studio.py app/ui/tauri_settings.py app/ui/pet_window.py tests/unit/test_tauri_studio.py tests/ui/test_pet_window.py
git commit -m "feat: 接入Tauri角色工作室进程"
```

Expected:

```text
[feat/tauri-character-studio <hash>] feat: 接入Tauri角色工作室进程
```

## Task 5: Tauri Studio Rust Shell

**Files:**
- Create: `tools/studio-tauri/src-tauri/Cargo.toml`
- Create: `tools/studio-tauri/src-tauri/build.rs`
- Create: `tools/studio-tauri/src-tauri/src/main.rs`
- Create: `tools/studio-tauri/src-tauri/src/lib.rs`
- Create: `tools/studio-tauri/src-tauri/tauri.conf.json`
- Create: `tools/studio-tauri/src-tauri/capabilities/default.json`

- [ ] **Step 1: Create Cargo manifest and Tauri config**

Create `tools/studio-tauri/src-tauri/Cargo.toml`:

```toml
[package]
name = "sakura-studio"
version = "0.1.0"
edition = "2021"
build = "build.rs"

[build-dependencies]
tauri-build = { version = "2", features = [] }

[dependencies]
serde = { version = "1", features = ["derive"] }
serde_json = "1"
tauri = { version = "2", features = [] }
tauri-plugin-dialog = "2"
```

Create `tools/studio-tauri/src-tauri/build.rs`:

```rust
fn main() {
    tauri_build::build()
}
```

Create `tools/studio-tauri/src-tauri/src/main.rs`:

```rust
fn main() {
    sakura_studio_lib::run()
}
```

Create `tools/studio-tauri/src-tauri/tauri.conf.json`:

```json
{
  "$schema": "https://schema.tauri.app/config/2",
  "productName": "Sakura Studio",
  "version": "0.1.0",
  "identifier": "com.rvosy.sakura.studio",
  "build": {
    "frontendDist": "../frontend"
  },
  "app": {
    "withGlobalTauri": true,
    "windows": [
      {
        "label": "main",
        "title": "Sakura 角色工作室",
        "width": 1120,
        "height": 760,
        "minWidth": 960,
        "minHeight": 640,
        "resizable": true,
        "maximizable": true,
        "center": true
      }
    ],
    "security": {
      "csp": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' asset: data:; font-src 'self'; connect-src 'self' ipc: http://ipc.localhost; object-src 'none'; frame-ancestors 'none'",
      "capabilities": ["default"]
    }
  },
  "bundle": {
    "active": false,
    "targets": "all"
  }
}
```

Create `tools/studio-tauri/src-tauri/capabilities/default.json`:

```json
{
  "$schema": "../gen/schemas/desktop-schema.json",
  "identifier": "default",
  "description": "Sakura character studio window",
  "windows": ["main"],
  "permissions": [
    "core:default",
    "core:window:allow-close",
    "dialog:allow-open",
    "dialog:allow-save"
  ]
}
```

- [ ] **Step 2: Implement Rust JSONL bridge**

Create `tools/studio-tauri/src-tauri/src/lib.rs` with:

```rust
use std::collections::HashMap;
use std::io::{BufRead, BufReader, Write};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{mpsc, Arc, Mutex};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde_json::{json, Value};
use tauri::{Emitter, Manager, State, Window, WindowEvent};

const RPC_MARKER: &str = "@@SAKURA_STUDIO_RPC@@";
const RPC_RESULT_MARKER: &str = "@@SAKURA_STUDIO_RPC_RESULT@@";
const CLOSE_REQUESTED_EVENT: &str = "sakura://studio-close-requested";
const DEFAULT_HOST_RPC_TIMEOUT: Duration = Duration::from_secs(30);
const FILE_RPC_TIMEOUT: Duration = Duration::from_secs(30 * 60);
static RPC_COUNTER: AtomicU64 = AtomicU64::new(1);

#[derive(Clone)]
struct AppState {
    request: Value,
    rpc: HostRpc,
}

#[derive(Clone)]
struct HostRpc {
    pending: Arc<Mutex<HashMap<String, mpsc::Sender<RpcResponse>>>>,
}

struct RpcResponse {
    id: String,
    ok: bool,
    result: Option<Value>,
    error: Option<String>,
}

impl HostRpc {
    fn new() -> Self {
        Self {
            pending: Arc::new(Mutex::new(HashMap::new())),
        }
    }

    fn call(&self, method: &str, params: Value) -> Result<Value, String> {
        let id = next_rpc_id();
        let (tx, rx) = mpsc::channel();
        self.pending
            .lock()
            .map_err(|_| "RPC pending map is poisoned".to_string())?
            .insert(id.clone(), tx);
        let payload = json!({ "id": id, "method": method, "params": params });
        let line = serde_json::to_string(&payload).map_err(|error| error.to_string())?;
        let write_result = (|| -> Result<(), String> {
            let mut out = std::io::stdout().lock();
            writeln!(out, "{RPC_MARKER}{line}").map_err(|error| error.to_string())?;
            out.flush().map_err(|error| error.to_string())?;
            Ok(())
        })();
        if let Err(error) = write_result {
            self.remove_pending(&id);
            return Err(error);
        }
        match rx.recv_timeout(host_rpc_timeout(method)) {
            Ok(response) if response.ok => Ok(response.result.unwrap_or(Value::Null)),
            Ok(response) => Err(response.error.unwrap_or_else(|| "host RPC returned an error".to_string())),
            Err(mpsc::RecvTimeoutError::Timeout) => {
                self.remove_pending(&id);
                Err("host RPC timed out".to_string())
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => {
                self.remove_pending(&id);
                Err("host RPC channel disconnected".to_string())
            }
        }
    }

    fn remove_pending(&self, id: &str) {
        if let Ok(mut pending) = self.pending.lock() {
            pending.remove(id);
        }
    }
}

#[tauri::command]
fn load_request(state: State<'_, AppState>) -> Result<Value, String> {
    Ok(state.request.clone())
}

#[tauri::command]
fn host_call(method: String, params: Value, state: State<'_, AppState>) -> Result<Value, String> {
    state.rpc.call(&method, params)
}

#[tauri::command]
fn close_studio(window: Window) -> Result<(), String> {
    let app = window.app_handle().clone();
    window.destroy().map_err(|error| error.to_string())?;
    app.exit(0);
    Ok(())
}

pub fn run() {
    let (request, rpc) = match read_request_and_spawn_rpc_reader() {
        Ok(state) => state,
        Err(error) => {
            eprintln!("{error}");
            std::process::exit(2);
        }
    };
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(AppState { request, rpc })
        .invoke_handler(tauri::generate_handler![load_request, host_call, close_studio])
        .on_window_event(|window, event| match event {
            WindowEvent::CloseRequested { api, .. } => {
                api.prevent_close();
                let _ = window.emit(CLOSE_REQUESTED_EVENT, json!({}));
            }
            WindowEvent::Destroyed => {
                window.app_handle().exit(0);
            }
            _ => {}
        })
        .run(tauri::generate_context!())
        .expect("failed to run Sakura character studio");
}

fn read_request_and_spawn_rpc_reader() -> Result<(Value, HostRpc), String> {
    let mut reader = BufReader::new(std::io::stdin());
    let mut data = String::new();
    let bytes = reader.read_line(&mut data).map_err(|error| error.to_string())?;
    if bytes == 0 {
        return Err("request payload is empty".to_string());
    }
    let value: Value = serde_json::from_str(data.trim_end()).map_err(|error| error.to_string())?;
    if !matches!(value, Value::Object(_)) {
        return Err("request payload must be a JSON object".to_string());
    }
    let rpc = HostRpc::new();
    let pending = rpc.pending.clone();
    std::thread::spawn(move || {
        let mut line = String::new();
        loop {
            line.clear();
            match reader.read_line(&mut line) {
                Ok(0) => break,
                Ok(_) => {
                    if let Some(response) = parse_rpc_response_line(line.trim_end()) {
                        if let Ok(mut pending) = pending.lock() {
                            if let Some(sender) = pending.remove(&response.id) {
                                let _ = sender.send(response);
                            }
                        }
                    }
                }
                Err(_) => break,
            }
        }
    });
    Ok((value, rpc))
}

fn parse_rpc_response_line(line: &str) -> Option<RpcResponse> {
    let payload = line.strip_prefix(RPC_RESULT_MARKER)?;
    let value: Value = serde_json::from_str(payload).ok()?;
    let id = value.get("id")?.as_str()?.to_string();
    let ok = value.get("ok")?.as_bool()?;
    let result = value.get("result").cloned();
    let error = value.get("error").and_then(Value::as_str).map(ToString::to_string);
    Some(RpcResponse { id, ok, result, error })
}

fn host_rpc_timeout(method: &str) -> Duration {
    match method {
        "studio.open_character"
        | "studio.save_character"
        | "studio.import_portrait"
        | "studio.export_archive" => FILE_RPC_TIMEOUT,
        _ => DEFAULT_HOST_RPC_TIMEOUT,
    }
}

fn next_rpc_id() -> String {
    let counter = RPC_COUNTER.fetch_add(1, Ordering::Relaxed);
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_nanos())
        .unwrap_or_default();
    format!("studio-{nanos}-{counter}")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_jsonl_rpc_response_with_matching_id() {
        let line = r#"@@SAKURA_STUDIO_RPC_RESULT@@{"id":"rpc-1","ok":true,"result":{"count":1}}"#;
        let response = parse_rpc_response_line(line).expect("response should parse");
        assert_eq!(response.id, "rpc-1");
        assert!(response.ok);
        assert_eq!(response.result.unwrap()["count"], 1);
    }

    #[test]
    fn ignores_invalid_rpc_response_lines() {
        assert!(parse_rpc_response_line("plain log").is_none());
        assert!(parse_rpc_response_line("@@SAKURA_STUDIO_RPC_RESULT@@not-json").is_none());
        assert!(parse_rpc_response_line(r#"@@SAKURA_STUDIO_RPC_RESULT@@{"id":"rpc-1"}"#).is_none());
    }

    #[test]
    fn parses_jsonl_rpc_error_response() {
        let line = r#"@@SAKURA_STUDIO_RPC_RESULT@@{"id":"rpc-2","ok":false,"error":"failed"}"#;
        let response = parse_rpc_response_line(line).expect("response should parse");
        assert_eq!(response.id, "rpc-2");
        assert!(!response.ok);
        assert_eq!(response.error.as_deref(), Some("failed"));
    }

    #[test]
    fn uses_long_timeout_for_file_rpc() {
        assert_eq!(host_rpc_timeout("studio.open_character"), Duration::from_secs(30 * 60));
        assert_eq!(host_rpc_timeout("studio.save_character"), Duration::from_secs(30 * 60));
        assert_eq!(host_rpc_timeout("studio.import_portrait"), Duration::from_secs(30 * 60));
        assert_eq!(host_rpc_timeout("studio.export_archive"), Duration::from_secs(30 * 60));
        assert_eq!(host_rpc_timeout("studio.list_characters"), Duration::from_secs(30));
    }
}
```

- [ ] **Step 3: Run Cargo tests**

Run:

```powershell
cargo test --manifest-path tools/studio-tauri/src-tauri/Cargo.toml
```

Expected:

```text
test result: ok. 4 passed
```

- [ ] **Step 4: Build the Studio binary**

Run:

```powershell
cargo build --manifest-path tools/studio-tauri/src-tauri/Cargo.toml
```

Expected:

```text
Finished dev [unoptimized + debuginfo] target(s) in
```

- [ ] **Step 5: Re-run Python binary resolver test**

Run:

```powershell
.\runtime\python.exe -m pytest tests/unit/test_tauri_studio.py::test_resolve_tauri_studio_binary_uses_env_and_platform -q
```

Expected:

```text
1 passed
```

- [ ] **Step 6: Commit Rust shell**

Run:

```powershell
git add tools/studio-tauri/src-tauri tests/unit/test_tauri_studio.py
git commit -m "feat: 新增Tauri角色工作室外壳"
```

Expected:

```text
[feat/tauri-character-studio <hash>] feat: 新增Tauri角色工作室外壳
```

## Task 6: Tauri Studio Frontend

**Files:**
- Create: `tools/studio-tauri/frontend/index.html`
- Create: `tools/studio-tauri/frontend/studio.js`
- Create: `tools/studio-tauri/frontend/styles.css`
- Modify: `tests/ui/test_pet_window.py`

- [ ] **Step 1: Add frontend static tests**

Append to `tests/ui/test_pet_window.py`:

```python
def test_tauri_studio_frontend_matches_settings_language() -> None:
    index = Path("tools/studio-tauri/frontend/index.html").read_text(encoding="utf-8")
    source = Path("tools/studio-tauri/frontend/studio.js").read_text(encoding="utf-8")
    styles = Path("tools/studio-tauri/frontend/styles.css").read_text(encoding="utf-8")

    assert "nav-card" in index
    assert "detail-card" in index
    assert "page-head" in index
    assert "settings-group" in index
    assert "角色工作室" in index
    assert "保存" in index
    assert "发布" not in index
    assert "发布" not in source
    assert "hostCall(\"studio.list_characters\"" in source
    assert "hostCall(\"studio.open_character\"" in source
    assert "hostCall(\"studio.create_character\"" in source
    assert "hostCall(\"studio.save_character\"" in source
    assert "hostCall(\"studio.import_portrait\"" in source
    assert "hostCall(\"studio.export_archive\"" in source
    assert "--sakura-primary" in styles
    assert "--motion-medium" in styles
    assert ".settings-page.is-active" in styles
```

- [ ] **Step 2: Run static frontend test to verify it fails**

Run:

```powershell
.\runtime\python.exe -m pytest tests/ui/test_pet_window.py::test_tauri_studio_frontend_matches_settings_language -q
```

Expected:

```text
FileNotFoundError: [Errno 2] No such file or directory: 'tools/studio-tauri/frontend/index.html'
```

- [ ] **Step 3: Create Studio HTML**

Create `tools/studio-tauri/frontend/index.html` with:

```html
<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Sakura 角色工作室</title>
    <link rel="stylesheet" href="./styles.css" />
  </head>
  <body>
    <main class="settings-shell studio-shell">
      <aside class="nav-card" aria-label="角色工作室导航">
        <div class="nav-brand">
          <span class="nav-brand-mark">S</span>
          <span class="nav-brand-text">角色工作室</span>
        </div>
        <nav class="nav-list">
          <div class="nav-group" role="group" aria-labelledby="navgrp-studio">
            <p class="nav-group-label" id="navgrp-studio">工作区</p>
            <button class="nav-item is-active" type="button" data-page="library" aria-current="page">
              <span class="nav-item-icon">◆</span>
              <span class="nav-item-label">角色列表</span>
            </button>
            <button class="nav-item" type="button" data-page="basic">
              <span class="nav-item-icon">◇</span>
              <span class="nav-item-label">基础信息</span>
            </button>
            <button class="nav-item" type="button" data-page="card">
              <span class="nav-item-icon">✦</span>
              <span class="nav-item-label">人设卡</span>
            </button>
            <button class="nav-item" type="button" data-page="portrait">
              <span class="nav-item-icon">◈</span>
              <span class="nav-item-label">立绘</span>
            </button>
            <button class="nav-item" type="button" data-page="theme">
              <span class="nav-item-icon">✧</span>
              <span class="nav-item-label">配色</span>
            </button>
          </div>
        </nav>
      </aside>
      <section class="detail-card">
        <header class="page-head">
          <h1 id="pageTitle" class="page-title">角色列表</h1>
          <p id="pageSubtitle" class="page-subtitle">打开本地角色或创建新角色</p>
        </header>
        <div class="page-scroll">
          <section id="page-library" class="settings-page is-active">
            <fieldset class="settings-group">
              <legend>本地角色</legend>
              <div class="studio-toolbar">
                <input id="characterSearch" type="search" placeholder="搜索角色" autocomplete="off" />
                <button id="newCharacterButton" type="button" class="secondary-button">新建角色</button>
              </div>
              <div id="characterList" class="character-list"></div>
            </fieldset>
          </section>
          <section id="page-basic" class="settings-page">
            <fieldset class="settings-group">
              <legend>基础信息</legend>
              <div class="setting-row">
                <label class="setting-row-text" for="characterId">
                  <span class="setting-title">角色 ID</span>
                  <span class="setting-desc">保存后的本地目录名。</span>
                </label>
                <input id="characterId" type="text" autocomplete="off" />
              </div>
              <div class="setting-row">
                <label class="setting-row-text" for="displayName">
                  <span class="setting-title">显示名称</span>
                  <span class="setting-desc">设置页和桌宠界面显示的名字。</span>
                </label>
                <input id="displayName" type="text" autocomplete="off" />
              </div>
              <div class="setting-row setting-row-block">
                <label class="setting-row-text" for="initialMessage">
                  <span class="setting-title">开场白</span>
                  <span class="setting-desc">角色首次进入对话时使用。</span>
                </label>
                <textarea id="initialMessage" rows="4"></textarea>
              </div>
              <div id="voiceStatusRow" class="setting-row" hidden>
                <div class="setting-row-text">
                  <span class="setting-title">语音</span>
                  <span id="voiceStatus" class="setting-desc"></span>
                </div>
              </div>
            </fieldset>
          </section>
          <section id="page-card" class="settings-page">
            <fieldset class="settings-group">
              <legend>人设卡</legend>
              <div class="setting-row setting-row-block">
                <label class="setting-row-text" for="cardText">
                  <span class="setting-title">系统人设</span>
                  <span class="setting-desc">保存为角色包内的 card.md。</span>
                </label>
                <textarea id="cardText" class="code-textarea" rows="16"></textarea>
              </div>
              <div class="setting-row setting-row-block">
                <label class="setting-row-text" for="replyToneInput">
                  <span class="setting-title">回复语气</span>
                  <span class="setting-desc">用逗号分隔多个语气标签。</span>
                </label>
                <input id="replyToneInput" type="text" autocomplete="off" />
              </div>
            </fieldset>
          </section>
          <section id="page-portrait" class="settings-page">
            <fieldset class="settings-group">
              <legend>立绘</legend>
              <div class="setting-row">
                <label class="setting-row-text" for="defaultPortrait">
                  <span class="setting-title">默认立绘</span>
                  <span class="setting-desc">角色包内相对路径。</span>
                </label>
                <div class="path-control">
                  <input id="defaultPortrait" type="text" autocomplete="off" />
                  <button id="importDefaultPortraitButton" type="button" class="secondary-button">导入</button>
                </div>
              </div>
              <div class="setting-row setting-row-block">
                <div class="setting-row-text">
                  <span class="setting-title">表情立绘</span>
                  <span class="setting-desc">标签与图片路径的映射。</span>
                </div>
                <div id="expressionList" class="expression-list"></div>
                <button id="addExpressionButton" type="button" class="secondary-button">添加表情</button>
              </div>
            </fieldset>
          </section>
          <section id="page-theme" class="settings-page">
            <fieldset class="settings-group">
              <legend>角色配色</legend>
              <div id="themeFields" class="theme-grid"></div>
            </fieldset>
          </section>
        </div>
        <footer class="settings-footer">
          <p id="errorText" class="error-text" role="alert"></p>
          <div class="footer-actions">
            <button id="exportButton" type="button" class="secondary-button">导出 .char</button>
            <button id="cancelButton" type="button" class="secondary-button">关闭</button>
            <button id="saveButton" type="button">保存</button>
          </div>
        </footer>
      </section>
    </main>
    <div id="toastStack" class="toast-stack" aria-live="polite" aria-atomic="false"></div>
    <script type="module" src="./studio.js"></script>
  </body>
</html>
```

- [ ] **Step 4: Create Studio JavaScript**

Create `tools/studio-tauri/frontend/studio.js` with the core implementation. It must include these functions exactly so tests and future maintainers can find the behavior:

```javascript
const invoke = window.__TAURI__.core.invoke;

const fields = {
  pageTitle: document.getElementById("pageTitle"),
  pageSubtitle: document.getElementById("pageSubtitle"),
  navItems: Array.from(document.querySelectorAll(".nav-item[data-page]")),
  pages: {
    library: document.getElementById("page-library"),
    basic: document.getElementById("page-basic"),
    card: document.getElementById("page-card"),
    portrait: document.getElementById("page-portrait"),
    theme: document.getElementById("page-theme"),
  },
  characterSearch: document.getElementById("characterSearch"),
  characterList: document.getElementById("characterList"),
  newCharacterButton: document.getElementById("newCharacterButton"),
  characterId: document.getElementById("characterId"),
  displayName: document.getElementById("displayName"),
  initialMessage: document.getElementById("initialMessage"),
  voiceStatusRow: document.getElementById("voiceStatusRow"),
  voiceStatus: document.getElementById("voiceStatus"),
  cardText: document.getElementById("cardText"),
  replyToneInput: document.getElementById("replyToneInput"),
  defaultPortrait: document.getElementById("defaultPortrait"),
  importDefaultPortraitButton: document.getElementById("importDefaultPortraitButton"),
  expressionList: document.getElementById("expressionList"),
  addExpressionButton: document.getElementById("addExpressionButton"),
  themeFields: document.getElementById("themeFields"),
  errorText: document.getElementById("errorText"),
  exportButton: document.getElementById("exportButton"),
  cancelButton: document.getElementById("cancelButton"),
  saveButton: document.getElementById("saveButton"),
  pageHead: document.querySelector(".page-head"),
};

const pageMeta = {
  library: { title: "角色列表", subtitle: "打开本地角色或创建新角色" },
  basic: { title: "基础信息", subtitle: "名称、开场白与语音状态" },
  card: { title: "人设卡", subtitle: "系统人设与回复语气" },
  portrait: { title: "立绘", subtitle: "默认立绘与表情映射" },
  theme: { title: "配色", subtitle: "角色包自带主题色" },
};

const themeLabels = {
  primary_color: "主色",
  primary_hover_color: "主色悬停",
  accent_color: "强调色",
  text_color: "正文",
  secondary_text_color: "次级文字",
  muted_text_color: "弱文字",
  page_background_color: "页面背景",
  panel_background_color: "面板背景",
  input_background_color: "输入背景",
  bubble_background_color: "气泡背景",
  border_color: "边框",
};

let request = null;
let currentPackageDir = "";
let currentDoc = null;
let baseline = "";
let busy = false;

function setError(message) {
  fields.errorText.textContent = message || "";
}

function notify(message, type = "info") {
  const text = String(message || "").trim();
  if (!text) {
    return;
  }
  if (type === "error") {
    setError(text);
    return;
  }
  const stack = document.getElementById("toastStack");
  const toast = document.createElement("div");
  toast.className = `toast is-${type}`;
  toast.textContent = text;
  stack.append(toast);
  window.setTimeout(() => {
    toast.classList.add("is-leaving");
    window.setTimeout(() => toast.remove(), 220);
  }, 2600);
}

async function hostCall(method, params = {}) {
  return invoke("host_call", { method, params });
}

function switchPage(page) {
  if (!fields.pages[page]) {
    return;
  }
  fields.navItems.forEach((item) => {
    const active = item.dataset.page === page;
    item.classList.toggle("is-active", active);
    item.toggleAttribute("aria-current", active);
  });
  Object.entries(fields.pages).forEach(([key, element]) => {
    element.classList.toggle("is-active", key === page);
  });
  const meta = pageMeta[page];
  fields.pageTitle.textContent = meta.title;
  fields.pageSubtitle.textContent = meta.subtitle;
  fields.pageHead.classList.remove("is-switching");
  void fields.pageHead.offsetWidth;
  fields.pageHead.classList.add("is-switching");
}

function renderCharacters() {
  const query = fields.characterSearch.value.trim().toLowerCase();
  const items = (request?.characters || []).filter((item) => {
    const haystack = `${item.display_name || ""} ${item.id || ""}`.toLowerCase();
    return !query || haystack.includes(query);
  });
  fields.characterList.textContent = "";
  if (!items.length) {
    const empty = document.createElement("p");
    empty.className = "empty-text";
    empty.textContent = "没有匹配的角色。";
    fields.characterList.append(empty);
    return;
  }
  items.forEach((character) => {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "character-row";
    row.dataset.id = character.id;
    const title = document.createElement("span");
    title.className = "character-row-title";
    title.textContent = character.display_name || character.id;
    const meta = document.createElement("span");
    meta.className = "character-row-meta";
    meta.textContent = `${character.id}${character.is_current ? " · 当前" : ""}${character.has_voice ? " · 含语音" : ""}`;
    row.append(title, meta);
    row.addEventListener("click", () => openCharacter(character.id));
    fields.characterList.append(row);
  });
}

function collectDoc() {
  const theme = currentDoc?.theme || {};
  fields.themeFields.querySelectorAll("[data-theme-key]").forEach((input) => {
    theme[input.dataset.themeKey] = input.value.trim();
  });
  const expressions = {};
  fields.expressionList.querySelectorAll(".expression-row").forEach((row) => {
    const label = row.querySelector("[data-expression-label]").value.trim();
    const path = row.querySelector("[data-expression-path]").value.trim();
    if (label && path) {
      expressions[label] = path;
    }
  });
  return {
    ...(currentDoc || {}),
    id: fields.characterId.value.trim(),
    display_name: fields.displayName.value.trim(),
    initial_message: fields.initialMessage.value,
    card_text: fields.cardText.value,
    reply_tones: fields.replyToneInput.value.split(/[,，]/).map((tone) => tone.trim()).filter(Boolean),
    default_portrait: fields.defaultPortrait.value.trim(),
    expressions,
    theme,
  };
}

function markBaseline() {
  baseline = JSON.stringify(collectDoc());
  refreshDirty();
}

function refreshDirty() {
  const dirty = currentDoc && JSON.stringify(collectDoc()) !== baseline;
  document.body.classList.toggle("is-dirty", Boolean(dirty));
  fields.saveButton.classList.toggle("has-changes", Boolean(dirty));
}

function setCurrentDoc(payload) {
  currentPackageDir = payload.package_dir || "";
  currentDoc = payload.doc || null;
  if (Array.isArray(payload.characters)) {
    request.characters = payload.characters;
    renderCharacters();
  }
  renderEditor();
  switchPage("basic");
  markBaseline();
}

function renderEditor() {
  const doc = currentDoc || {};
  fields.characterId.value = doc.id || "";
  fields.characterId.disabled = Boolean(doc.id);
  fields.displayName.value = doc.display_name || "";
  fields.initialMessage.value = doc.initial_message || "";
  fields.cardText.value = doc.card_text || "";
  fields.replyToneInput.value = Array.isArray(doc.reply_tones) ? doc.reply_tones.join("，") : "";
  fields.defaultPortrait.value = doc.default_portrait || "";
  fields.voiceStatusRow.hidden = false;
  fields.voiceStatus.textContent = doc.voice ? "已保留现有语音配置" : "未配置语音";
  renderExpressions(doc.expressions || {});
  renderTheme(doc.theme || request.theme || {});
  refreshControls();
}

function renderExpressions(expressions) {
  fields.expressionList.textContent = "";
  Object.entries(expressions).forEach(([label, path]) => addExpressionRow(label, path));
}

function addExpressionRow(label = "", path = "") {
  const row = document.createElement("div");
  row.className = "expression-row";
  const labelInput = document.createElement("input");
  labelInput.type = "text";
  labelInput.value = label;
  labelInput.placeholder = "标签";
  labelInput.dataset.expressionLabel = "1";
  const pathInput = document.createElement("input");
  pathInput.type = "text";
  pathInput.value = path;
  pathInput.placeholder = "portraits/example.png";
  pathInput.dataset.expressionPath = "1";
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "secondary-button icon-button";
  remove.textContent = "×";
  remove.addEventListener("click", () => {
    row.remove();
    refreshDirty();
  });
  row.append(labelInput, pathInput, remove);
  row.addEventListener("input", refreshDirty);
  fields.expressionList.append(row);
}

function renderTheme(theme) {
  fields.themeFields.textContent = "";
  (request.theme_fields || Object.keys(themeLabels)).forEach((key) => {
    const row = document.createElement("label");
    row.className = "theme-field";
    const swatch = document.createElement("span");
    swatch.className = "theme-swatch";
    const text = document.createElement("span");
    text.textContent = themeLabels[key] || key;
    const input = document.createElement("input");
    input.type = "text";
    input.value = theme[key] || "";
    input.dataset.themeKey = key;
    const update = () => {
      swatch.style.background = input.value.trim() || "transparent";
      refreshDirty();
    };
    input.addEventListener("input", update);
    row.append(swatch, text, input);
    fields.themeFields.append(row);
    update();
  });
}

async function openCharacter(characterId) {
  await runBusy(async () => {
    const payload = await hostCall("studio.open_character", { character_id: characterId });
    setCurrentDoc(payload);
  });
}

async function createCharacter() {
  const id = window.prompt("角色 ID：", "");
  if (!id) {
    return;
  }
  const displayName = window.prompt("显示名称：", id) || id;
  await runBusy(async () => {
    const payload = await hostCall("studio.create_character", {
      doc: { id, display_name: displayName },
    });
    setCurrentDoc(payload);
  });
}

async function importDefaultPortrait() {
  if (!currentDoc || !currentPackageDir) {
    setError("请先打开或新建角色。");
    return;
  }
  const selected = await window.__TAURI__?.dialog?.open({
    title: "导入默认立绘",
    multiple: false,
    filters: [{ name: "图片", extensions: ["png", "jpg", "jpeg", "webp", "gif"] }],
  });
  const path = Array.isArray(selected) ? selected[0] : selected;
  if (!path) {
    return;
  }
  await runBusy(async () => {
    const result = await hostCall("studio.import_portrait", {
      package_dir: currentPackageDir,
      path,
      label: "default",
    });
    fields.defaultPortrait.value = result.relative_path;
    refreshDirty();
  });
}

async function saveCharacter() {
  if (!currentDoc || !currentPackageDir) {
    setError("请先打开或新建角色。");
    return;
  }
  await runBusy(async () => {
    const payload = await hostCall("studio.save_character", {
      package_dir: currentPackageDir,
      current_character_id: request.initial_character_id || "",
      doc: collectDoc(),
    });
    if (Array.isArray(payload.characters)) {
      request.characters = payload.characters;
      renderCharacters();
    }
    currentDoc = payload.doc || collectDoc();
    markBaseline();
    notify(payload.message || "已保存。", "success");
  });
}

async function exportCharacter() {
  if (!currentDoc || !currentPackageDir) {
    setError("请先打开或新建角色。");
    return;
  }
  const defaultPath = `${fields.characterId.value.trim() || "character"}.char`;
  const path = await window.__TAURI__?.dialog?.save({
    title: "导出 Sakura 角色包",
    defaultPath,
    filters: [{ name: "Sakura 角色包", extensions: ["char"] }],
  });
  if (!path) {
    return;
  }
  await runBusy(async () => {
    await hostCall("studio.save_draft", { package_dir: currentPackageDir, doc: collectDoc() });
    const result = await hostCall("studio.export_archive", {
      package_dir: currentPackageDir,
      path,
      include_voice: false,
    });
    notify(result.message || "角色包已导出。", "success");
  });
}

async function runBusy(action) {
  if (busy) {
    return;
  }
  busy = true;
  refreshControls();
  setError("");
  try {
    await action();
  } catch (error) {
    setError(String(error));
  } finally {
    busy = false;
    refreshControls();
  }
}

function refreshControls() {
  const hasDoc = Boolean(currentDoc);
  fields.saveButton.disabled = busy || !hasDoc;
  fields.exportButton.disabled = busy || !hasDoc;
  fields.importDefaultPortraitButton.disabled = busy || !hasDoc;
}

async function closeStudio() {
  await invoke("close_studio");
}

async function load() {
  request = await invoke("load_request");
  document.documentElement.style.setProperty("--sakura-primary", request.theme?.primary_color || "#d55b91");
  renderCharacters();
  if (request.initial_character_id) {
    await openCharacter(request.initial_character_id);
  } else {
    refreshControls();
  }
}

fields.navItems.forEach((item) => item.addEventListener("click", () => switchPage(item.dataset.page)));
fields.characterSearch.addEventListener("input", renderCharacters);
fields.newCharacterButton.addEventListener("click", createCharacter);
fields.importDefaultPortraitButton.addEventListener("click", importDefaultPortrait);
fields.addExpressionButton.addEventListener("click", () => {
  addExpressionRow();
  refreshDirty();
});
fields.saveButton.addEventListener("click", saveCharacter);
fields.exportButton.addEventListener("click", exportCharacter);
fields.cancelButton.addEventListener("click", closeStudio);
[fields.characterId, fields.displayName, fields.initialMessage, fields.cardText, fields.replyToneInput, fields.defaultPortrait]
  .forEach((element) => element.addEventListener("input", refreshDirty));

load().catch((error) => setError(String(error)));
```

- [ ] **Step 5: Create Studio CSS**

Create `tools/studio-tauri/frontend/styles.css` by copying the theme variables and shared layout blocks from `tools/settings-tauri/frontend/styles.css`, then add these Studio-specific rules:

```css
.studio-shell {
  grid-template-columns: 190px minmax(0, 1fr);
}

.studio-toolbar {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 10px;
  align-items: center;
}

.character-list {
  display: grid;
  gap: 8px;
  min-width: 0;
}

.character-row {
  display: grid;
  gap: 3px;
  width: 100%;
  min-height: 58px;
  padding: 10px 12px;
  border: 1px solid var(--hairline);
  border-radius: 8px;
  background: var(--surface-raised);
  color: var(--sakura-text);
  font: inherit;
  text-align: left;
  cursor: pointer;
  transition:
    border-color var(--motion-base) var(--ease),
    box-shadow var(--motion-base) var(--ease),
    transform var(--motion-fast) var(--ease);
}

.character-row:hover {
  border-color: var(--sakura-primary);
  box-shadow: var(--shadow-pop);
  transform: translateY(-1px);
}

.character-row-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 700;
}

.character-row-meta,
.empty-text {
  margin: 0;
  color: var(--sakura-muted-text);
  font-size: 12px;
}

.setting-row-block {
  align-items: stretch;
  grid-template-columns: minmax(0, 1fr);
}

.setting-row-block .setting-row-text {
  max-width: none;
}

textarea {
  width: 100%;
  min-width: 0;
  resize: vertical;
}

.code-textarea {
  min-height: 320px;
  font-family: "Cascadia Code", "Consolas", monospace;
  line-height: 1.55;
}

.path-control {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 8px;
  min-width: 0;
}

.expression-list {
  display: grid;
  gap: 8px;
  min-width: 0;
}

.expression-row {
  display: grid;
  grid-template-columns: minmax(120px, 0.35fr) minmax(0, 1fr) 34px;
  gap: 8px;
  align-items: center;
}

.icon-button {
  width: 34px;
  min-width: 34px;
  padding: 0;
  text-align: center;
}

.theme-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.theme-field {
  display: grid;
  grid-template-columns: 24px 88px minmax(0, 1fr);
  gap: 8px;
  align-items: center;
  min-width: 0;
}

.theme-swatch {
  width: 22px;
  height: 22px;
  border: 1px solid var(--hairline);
  border-radius: 6px;
  background: var(--sakura-primary);
}

.settings-footer {
  display: flex;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
  min-height: 64px;
  padding: 12px 16px;
  border-top: 1px solid var(--hairline);
  background: color-mix(in srgb, var(--sakura-panel-bg) 64%, var(--surface-raised));
}

.footer-actions {
  display: flex;
  gap: 8px;
  align-items: center;
}

#saveButton.has-changes {
  box-shadow: 0 0 0 3px var(--ring);
}

@media (max-width: 860px) {
  .studio-shell {
    grid-template-columns: 1fr;
  }

  .nav-card {
    display: none;
  }

  .theme-grid,
  .expression-row {
    grid-template-columns: minmax(0, 1fr);
  }
}
```

- [ ] **Step 6: Run frontend static test**

Run:

```powershell
.\runtime\python.exe -m pytest tests/ui/test_pet_window.py::test_tauri_studio_frontend_matches_settings_language -q
```

Expected:

```text
1 passed
```

- [ ] **Step 7: Build Studio binary with frontend files present**

Run:

```powershell
cargo build --manifest-path tools/studio-tauri/src-tauri/Cargo.toml
```

Expected:

```text
Finished dev [unoptimized + debuginfo] target(s) in
```

- [ ] **Step 8: Commit Studio frontend**

Run:

```powershell
git add tools/studio-tauri/frontend tests/ui/test_pet_window.py
git commit -m "feat: 实现Tauri角色工作室前端"
```

Expected:

```text
[feat/tauri-character-studio <hash>] feat: 实现Tauri角色工作室前端
```

## Task 7: Settings Page Entry Points

**Files:**
- Modify: `tools/settings-tauri/frontend/index.html`
- Modify: `tools/settings-tauri/frontend/settings.js`
- Modify: `tools/settings-tauri/frontend/styles.css`
- Test: `tests/ui/test_pet_window.py`

- [ ] **Step 1: Add settings page Studio controls**

In `tools/settings-tauri/frontend/index.html`, after the existing `角色包` fieldset, add:

```html
<fieldset class="settings-group">
  <legend>角色工作室</legend>
  <div class="setting-row">
    <div class="setting-row-text">
      <span class="setting-title">本地角色编辑</span>
      <span class="setting-desc">打开与设置一致的 Tauri 工作室。</span>
    </div>
    <div class="archive-controls studio-controls">
      <button id="characterStudioCurrentButton" type="button" class="secondary-button">编辑当前角色</button>
      <button id="characterStudioOpenButton" type="button" class="secondary-button">打开角色工作室</button>
    </div>
  </div>
</fieldset>
```

- [ ] **Step 2: Wire settings buttons to `studio.launch`**

In the `fields` object in `tools/settings-tauri/frontend/settings.js`, add:

```javascript
characterStudioCurrentButton: document.getElementById("characterStudioCurrentButton"),
characterStudioOpenButton: document.getElementById("characterStudioOpenButton"),
```

Add:

```javascript
async function launchCharacterStudio(characterId = "") {
  if (characterArchiveBusy) {
    return;
  }
  setError("");
  setCharacterArchiveBusy(true);
  try {
    const result = await hostCall("studio.launch", { character_id: characterId || "" });
    if (result?.message) {
      notify(result.message, "success");
    }
  } catch (error) {
    setError(String(error));
  } finally {
    setCharacterArchiveBusy(false);
  }
}
```

In `syncCharacterArchiveState`, add:

```javascript
fields.characterStudioCurrentButton.disabled = characterArchiveBusy || !hasCharacter;
fields.characterStudioOpenButton.disabled = characterArchiveBusy;
```

Near the existing character archive event listeners, add:

```javascript
fields.characterStudioCurrentButton.addEventListener("click", () => {
  const character = selectedCharacter();
  launchCharacterStudio(character?.id || "");
});
fields.characterStudioOpenButton.addEventListener("click", () => launchCharacterStudio(""));
```

- [ ] **Step 3: Run settings frontend entry test**

Run:

```powershell
.\runtime\python.exe -m pytest tests/ui/test_pet_window.py::test_tauri_settings_frontend_has_studio_buttons_without_publish_wording tests/ui/test_pet_window.py::test_tauri_settings_dispatches_studio_launch_callback -q
```

Expected:

```text
2 passed
```

- [ ] **Step 4: Commit settings entry points**

Run:

```powershell
git add tools/settings-tauri/frontend/index.html tools/settings-tauri/frontend/settings.js tools/settings-tauri/frontend/styles.css tests/ui/test_pet_window.py app/ui/tauri_settings.py
git commit -m "feat: 设置页接入角色工作室入口"
```

Expected:

```text
[feat/tauri-character-studio <hash>] feat: 设置页接入角色工作室入口
```

## Task 8: Verification and Polish

**Files:**
- Read: `tools/studio-tauri/frontend/index.html`
- Read: `tools/studio-tauri/frontend/studio.js`
- Read: `tools/studio-tauri/frontend/styles.css`
- Read: `tools/settings-tauri/frontend/index.html`
- Read: `tools/settings-tauri/frontend/settings.js`
- Read: `app/config/character_studio.py`
- Read: `app/ui/tauri_studio.py`
- Read: `app/ui/tauri_settings.py`
- Read: `app/ui/pet_window.py`

- [ ] **Step 1: Scan for disallowed wording**

Run:

```powershell
rg -n "发布|publish|Publish" tools/studio-tauri tools/settings-tauri/frontend app/config/character_studio.py app/ui/tauri_studio.py app/ui/tauri_settings.py app/ui/pet_window.py
```

Expected:

```text
No matches.
```

- [ ] **Step 2: Run targeted Python tests**

Run:

```powershell
.\runtime\python.exe -m pytest tests/unit/test_character_studio.py tests/unit/test_tauri_studio.py tests/unit/test_character_archive.py tests/ui/test_studio.py -q
```

Expected:

```text
passed
```

- [ ] **Step 3: Run targeted PetWindow Tauri settings tests**

Run:

```powershell
.\runtime\python.exe -m pytest tests/ui/test_pet_window.py::test_tauri_settings_dispatches_studio_launch_callback tests/ui/test_pet_window.py::test_tauri_settings_dispatches_studio_launch_failure tests/ui/test_pet_window.py::test_tauri_settings_frontend_has_studio_buttons_without_publish_wording tests/ui/test_pet_window.py::test_tauri_studio_frontend_matches_settings_language tests/ui/test_pet_window.py::test_tauri_settings_process_writes_memory_rpc_response_line tests/ui/test_pet_window.py::test_tauri_character_rpc_validates_paths -q
```

Expected:

```text
6 passed
```

- [ ] **Step 4: Run Cargo tests and build both Tauri tools**

Run:

```powershell
cargo test --manifest-path tools/studio-tauri/src-tauri/Cargo.toml
cargo build --manifest-path tools/studio-tauri/src-tauri/Cargo.toml
cargo test --manifest-path tools/settings-tauri/src-tauri/Cargo.toml
cargo build --manifest-path tools/settings-tauri/src-tauri/Cargo.toml
```

Expected:

```text
test result: ok.
Finished dev [unoptimized + debuginfo] target(s) in
```

- [ ] **Step 5: Run full Python test suite before PR**

Run:

```powershell
.\runtime\python.exe -m pytest
```

Expected:

```text
passed
```

- [ ] **Step 6: Manual smoke test**

Run Sakura:

```powershell
.\runtime\python.exe main.py
```

Expected manual checks:

```text
1. 打开设置。
2. 进入角色页。
3. 点击“编辑当前角色”，Tauri 角色工作室打开并自动载入当前角色。
4. 修改显示名称或人设卡，点击“保存”。
5. config/characters.yaml 的 current_character_id 不变。
6. characters/<id>/character.json 与 card.md 已更新。
7. 关闭工作室，再点“打开角色工作室”，角色列表出现所有本地角色。
8. 新建角色、导入默认立绘、保存后，新角色目录出现在 characters/<new_id>/。
9. 设置页和角色工作室的导航、设置组、底栏、动效观感一致。
```

- [ ] **Step 7: Commit verification polish if files changed**

Run:

```powershell
git status --short
```

If the smoke test or polish changed source files, commit them:

```powershell
git add app tools tests
git commit -m "style: 打磨角色工作室体验"
```

Expected when a commit is needed:

```text
[feat/tauri-character-studio <hash>] style: 打磨角色工作室体验
```

Expected when no commit is needed:

```text
No source changes to commit.
```

## Task 9: PR Preparation

**Files:**
- Read: all changed files from `git diff --stat origin/dev...HEAD`

- [ ] **Step 1: Review branch diff**

Run:

```powershell
git diff --stat origin/dev...HEAD
git diff --check origin/dev...HEAD
```

Expected:

```text
<changed file summary>
```

and:

```text
No whitespace errors.
```

- [ ] **Step 2: Confirm final tests**

Run:

```powershell
.\runtime\python.exe -m pytest
cargo test --manifest-path tools/studio-tauri/src-tauri/Cargo.toml
cargo test --manifest-path tools/settings-tauri/src-tauri/Cargo.toml
```

Expected:

```text
passed
test result: ok.
test result: ok.
```

- [ ] **Step 3: Push branch**

Run:

```powershell
git push -u origin feat/tauri-character-studio
```

Expected:

```text
branch 'feat/tauri-character-studio' set up to track 'origin/feat/tauri-character-studio'
```

- [ ] **Step 4: Open PR to dev with Chinese summary**

Run:

```powershell
gh pr create --base dev --head feat/tauri-character-studio --title "feat: 新增Tauri角色工作室" --body "## 变更\n- 新增独立 Tauri 角色工作室，视觉语言对齐设置页\n- 支持本地角色列表、编辑、新建、立绘导入、保存与导出\n- 设置页角色区域新增工作室入口，保存后不切换当前角色\n\n## 验证\n- .\\runtime\\python.exe -m pytest\n- cargo test --manifest-path tools/studio-tauri/src-tauri/Cargo.toml\n- cargo test --manifest-path tools/settings-tauri/src-tauri/Cargo.toml"
```

Expected:

```text
https://github.com/<owner>/<repo>/pull/<number>
```

## Self-Review

- Spec coverage: The plan creates an independent `tools/studio-tauri` app, uses settings visual language, adds settings role entry points, lists installed characters, edits existing roles through a draft, creates new roles, saves locally with “保存”, avoids switching current role, and keeps voice editing out of first version while preserving existing voice resources.
- Placeholder scan: The plan contains concrete paths, concrete commands, expected outputs, and code snippets for tests and implementation steps.
- Type consistency: RPC method names are consistent across Python bridge, Rust host call, frontend tests, and frontend implementation: `studio.list_characters`, `studio.open_character`, `studio.create_character`, `studio.save_draft`, `studio.save_character`, `studio.import_portrait`, `studio.export_archive`, and settings-only `studio.launch`.
