# Tauri Character Studio UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 合并设置页重复入口，并让 Tauri 角色工作室通过持久角色下拉菜单和主设置同款配色控件完成角色编辑。

**Architecture:** 保留独立的设置进程和角色工作室进程。设置页只负责把当前选择的角色 ID 传入工作室；工作室前端维护独立的 `editingCharacterId`，Python 请求继续保留 `initial_character_id` 表示桌宠当前角色。主题字段契约由 `app.ui.theme.THEME_COLOR_FIELDS` 统一提供，前端复用主设置页配色编辑器的交互和样式。

**Tech Stack:** Python 3、PySide6、pytest、Tauri 2、Rust、原生 HTML/CSS/JavaScript

---

## 文件结构

- `tools/settings-tauri/frontend/index.html`：将角色工作室入口移动到当前角色下拉菜单旁。
- `tools/settings-tauri/frontend/settings.js`：删除重复按钮状态和事件，只启动当前下拉项。
- `tools/settings-tauri/frontend/styles.css`：增加角色下拉菜单与按钮的稳定横向布局。
- `app/ui/tauri_studio.py`：统一主题字段契约，并为工作室暴露屏幕取色 RPC。
- `app/ui/pet_window.py`：工作室存活期间继续压低桌宠置顶层级，保证屏幕取色器不被遮挡。
- `tools/studio-tauri/frontend/index.html`：移除角色列表页，增加持久角色选择器和主设置同款导航图标。
- `tools/studio-tauri/frontend/studio.js`：维护当前编辑角色、未保存切换保护和完整配色编辑器。
- `tools/studio-tauri/frontend/styles.css`：对齐主设置基础组件与配色编辑器样式，保留工作室专用表单布局。
- `tests/ui/test_pet_window.py`：覆盖设置入口、工作室结构和配色交互契约。
- `tests/unit/test_tauri_studio.py`：覆盖工作室请求主题字段和取色 RPC。

### Task 1: 合并设置页角色工作室入口

**Files:**
- Modify: `tests/ui/test_pet_window.py`
- Modify: `tools/settings-tauri/frontend/index.html`
- Modify: `tools/settings-tauri/frontend/settings.js`
- Modify: `tools/settings-tauri/frontend/styles.css`

- [ ] **Step 1: 写入失败的前端结构测试**

将现有 `test_tauri_settings_frontend_has_studio_buttons_without_publish_wording` 改为：

```python
def test_tauri_settings_frontend_has_single_character_editor_button() -> None:
    index = Path("tools/settings-tauri/frontend/index.html").read_text(encoding="utf-8")
    source = Path("tools/settings-tauri/frontend/settings.js").read_text(encoding="utf-8")

    assert 'id="characterEditorButton"' in index
    assert 'class="character-select-controls"' in index
    assert "characterStudioCurrentButton" not in index
    assert "characterStudioOpenButton" not in index
    assert "角色工作室</legend>" not in index
    assert 'hostCall("studio.launch", { character_id: character.id })' in source
    assert "characterStudioCurrentButton" not in source
    assert "characterStudioOpenButton" not in source
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `.\runtime\python.exe -m pytest tests/ui/test_pet_window.py::test_tauri_settings_frontend_has_single_character_editor_button -q`

Expected: FAIL，因为页面仍包含两个旧按钮且没有 `characterEditorButton`。

- [ ] **Step 3: 修改设置页 HTML**

将当前角色行的裸 `select` 替换为：

```html
<div class="character-select-controls">
  <select id="characterSelect"></select>
  <button id="characterEditorButton" type="button" class="secondary-button">修改角色</button>
</div>
```

删除整个 `<legend>角色工作室</legend>` 设置组。

- [ ] **Step 4: 收口设置页 JavaScript**

字段表只保留：

```javascript
characterEditorButton: document.getElementById("characterEditorButton"),
```

`syncCharacterArchiveState()` 使用：

```javascript
fields.characterEditorButton.disabled = characterArchiveBusy || !hasCharacter;
```

启动逻辑改为：

```javascript
async function launchCharacterStudio() {
  const character = selectedCharacter();
  if (!character) {
    setError("请先选择一个角色。");
    return;
  }
  await runCharacterArchiveAction(async () => {
    const result = await hostCall("studio.launch", { character_id: character.id });
    if (result?.message) {
      notify(result.message, "success");
    }
  });
}

fields.characterEditorButton.addEventListener("click", launchCharacterStudio);
```

删除两个旧按钮的字段、禁用状态和事件监听器。

- [ ] **Step 5: 添加稳定布局样式**

```css
.character-select-controls {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 10px;
  align-items: center;
  width: min(100%, 420px);
  min-width: 0;
}

.character-select-controls > select,
.character-select-controls > .custom-select {
  width: 100%;
  min-width: 0;
}

.character-select-controls > button {
  white-space: nowrap;
}
```

删除 `.studio-controls`。

- [ ] **Step 6: 运行测试并提交**

Run: `.\runtime\python.exe -m pytest tests/ui/test_pet_window.py::test_tauri_settings_frontend_has_single_character_editor_button tests/ui/test_pet_window.py::test_tauri_settings_dispatches_studio_launch_callback -q`

Expected: PASS。

```powershell
git add tests/ui/test_pet_window.py tools/settings-tauri/frontend/index.html tools/settings-tauri/frontend/settings.js tools/settings-tauri/frontend/styles.css
git commit -m "fix: 合并角色工作室入口"
```

### Task 2: 统一主题字段并增加工作室取色 RPC

**Files:**
- Modify: `tests/unit/test_tauri_studio.py`
- Modify: `tests/ui/test_pet_window.py`
- Modify: `app/ui/tauri_studio.py`
- Modify: `app/ui/pet_window.py`

- [ ] **Step 1: 写入失败的主题契约测试**

扩展 `test_build_tauri_studio_request_contains_characters_and_nonce`：

```python
from app.ui.theme import DEFAULT_THEME_SETTINGS, THEME_COLOR_FIELDS, theme_to_mapping

assert request["theme_defaults"] == theme_to_mapping(DEFAULT_THEME_SETTINGS)
assert request["theme_fields"] == [
    {"id": field, "label": label}
    for field, label, _default in THEME_COLOR_FIELDS
]
```

新增：

```python
def test_dispatch_tauri_studio_rpc_picks_screen_color(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    import app.ui.tauri_studio as tauri_studio

    monkeypatch.setattr(tauri_studio, "pick_screen_color", lambda: "#112233")
    assert tauri_studio.dispatch_tauri_studio_rpc(
        tmp_path,
        "studio.pick_screen_color",
        {},
    ) == {"color": "#112233"}

    monkeypatch.setattr(tauri_studio, "pick_screen_color", lambda: None)
    assert tauri_studio.dispatch_tauri_studio_rpc(
        tmp_path,
        "studio.pick_screen_color",
        {},
    ) == {"cancelled": True}
```

新增工作室置顶生命周期测试：

```python
def test_pet_window_syncs_topmost_while_tauri_studio_is_active() -> None:
    from app.ui.pet_window import PetWindow

    native_sync_events: list[bool] = []

    class Host:
        _sync_secondary_window_state = PetWindow._sync_secondary_window_state
        _is_secondary_window_visible = PetWindow._is_secondary_window_visible
        _set_secondary_windows_topmost_suppressed = PetWindow._set_secondary_windows_topmost_suppressed

        always_on_top_enabled = True
        tauri_settings_process = None
        tauri_studio_process = object()

        def __init__(self) -> None:
            self._registered_secondary_windows = set()
            self._secondary_windows_suppress_topmost = False

        def _sync_native_topmost_state(self) -> None:
            native_sync_events.append(self._secondary_windows_suppress_topmost)

        def isVisible(self) -> bool:
            return True

        def raise_(self) -> None:
            pass

    host = Host()
    host._sync_secondary_window_state()

    assert native_sync_events == [True]
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `.\runtime\python.exe -m pytest tests/unit/test_tauri_studio.py tests/ui/test_pet_window.py::test_pet_window_syncs_topmost_while_tauri_studio_is_active -q`

Expected: FAIL，当前 `theme_fields` 只有字段名、不存在取色 RPC，且置顶同步没有统计工作室进程。

- [ ] **Step 3: 修改工作室请求和 RPC**

导入：

```python
from app.ui.screen_color_picker import pick_screen_color
from app.ui.theme import DEFAULT_THEME_SETTINGS, THEME_COLOR_FIELDS, theme_to_mapping
```

请求字段改为：

```python
theme_defaults = theme_to_mapping(DEFAULT_THEME_SETTINGS)
return {
    "version": TAURI_STUDIO_PROTOCOL_VERSION,
    "nonce": nonce or uuid.uuid4().hex,
    "initial_character_id": str(initial_character_id or ""),
    "characters": service.list_characters(current_character_id=str(initial_character_id or "")),
    "theme": theme_defaults,
    "theme_defaults": theme_defaults,
    "theme_fields": [
        {"id": field, "label": label}
        for field, label, _default in THEME_COLOR_FIELDS
    ],
}
```

在 `dispatch_tauri_studio_rpc` 中加入：

```python
if method == "studio.pick_screen_color":
    color = pick_screen_color()
    if color is None:
        return {"cancelled": True}
    return {"color": color}
```

`PetWindow._sync_secondary_window_state()` 的独立 Tauri 进程判断改为：

```python
tauri_active = (
    getattr(self, "tauri_settings_process", None) is not None
    or getattr(self, "tauri_studio_process", None) is not None
)
```

工作室成功启动、关闭、失败和 shutdown 后均调用 `self._sync_secondary_window_state()`，确保设置窗口先关闭时仍保持压低置顶，最后一个 Tauri 窗口关闭后才恢复。

- [ ] **Step 4: 运行测试并提交**

Run: `.\runtime\python.exe -m pytest tests/unit/test_tauri_studio.py tests/ui/test_pet_window.py::test_pet_window_syncs_topmost_while_tauri_studio_is_active -q`

Expected: PASS。

```powershell
git add tests/unit/test_tauri_studio.py tests/ui/test_pet_window.py app/ui/tauri_studio.py app/ui/pet_window.py
git commit -m "fix: 统一角色工作室主题契约"
```

### Task 3: 用持久下拉菜单替换角色列表页

**Files:**
- Modify: `tests/ui/test_pet_window.py`
- Modify: `tools/studio-tauri/frontend/index.html`
- Modify: `tools/studio-tauri/frontend/studio.js`
- Modify: `tools/studio-tauri/frontend/styles.css`

- [ ] **Step 1: 写入失败的工作室结构测试**

在 `test_tauri_studio_frontend_matches_settings_language` 中增加：

```python
assert 'id="studioCharacterSelect"' in index
assert 'id="newCharacterButton"' in index
assert 'data-page="library"' not in index
assert 'id="page-library"' not in index
assert 'id="characterSearch"' not in index
assert 'id="refreshCharactersButton"' not in index
assert "editingCharacterId" in source
assert "confirmDiscardChanges" in source
assert 'hostCall("studio.open_character"' in source
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `.\runtime\python.exe -m pytest tests/ui/test_pet_window.py::test_tauri_studio_frontend_matches_settings_language -q`

Expected: FAIL，角色列表页和旧字段仍存在。

- [ ] **Step 3: 调整 HTML 导航和页头工具栏**

删除 `library` 导航按钮和 `page-library`。导航首项改为激活的“基础信息”，四个导航项均使用主设置页同结构的内联 SVG：

```html
<button class="nav-item is-active" type="button" data-page="basic" aria-current="page">
  <span class="nav-item-icon" aria-hidden="true">
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>
  </span>
  <span class="nav-item-label">基础信息</span>
</button>
```

在 `.page-head` 内标题区域后添加：

```html
<div class="studio-character-controls">
  <label for="studioCharacterSelect">当前编辑角色</label>
  <select id="studioCharacterSelect"></select>
  <button id="newCharacterButton" type="button" class="secondary-button">新建角色</button>
</div>
```

初始标题改为“基础信息”。

- [ ] **Step 4: 重写角色选择状态**

删除 `characterSearch`、`refreshCharactersButton`、`characterList`、`refreshCharacters()` 和 `renderCharacters()`。新增状态和完整辅助函数：

```javascript
let editingCharacterId = "";

function isDirty() {
  return Boolean(currentDoc) && JSON.stringify(collectDoc()) !== baseline;
}

function confirmDiscardChanges() {
  return !isDirty() || window.confirm("当前修改尚未保存，继续操作将丢失这些修改。是否继续？");
}

function characterOptionLabel(character) {
  const name = character.display_name || character.id;
  return character.source === "draft" ? `${name}（新建）` : name;
}

function renderCharacterOptions(extraCharacter = null) {
  const items = [...(request?.characters || [])];
  if (extraCharacter && !items.some((item) => item.id === extraCharacter.id)) {
    items.unshift(extraCharacter);
  }
  fields.studioCharacterSelect.textContent = "";
  items.forEach((character) => {
    const option = document.createElement("option");
    option.value = character.id;
    option.textContent = characterOptionLabel(character);
    fields.studioCharacterSelect.append(option);
  });
  fields.studioCharacterSelect.value = editingCharacterId;
}

async function selectCharacter(characterId) {
  const previousId = editingCharacterId;
  if (!characterId || characterId === previousId) {
    fields.studioCharacterSelect.value = previousId;
    return;
  }
  if (!confirmDiscardChanges()) {
    fields.studioCharacterSelect.value = previousId;
    return;
  }
  await runBusy(async () => {
    try {
      const payload = await hostCall("studio.open_character", { character_id: characterId });
      setCurrentDoc(payload);
    } catch (error) {
      fields.studioCharacterSelect.value = previousId;
      throw error;
    }
  });
}
```

`setCurrentDoc()` 必须设置 `editingCharacterId = payload.doc?.id || ""`，然后渲染下拉菜单、编辑器并切到 `basic`。

`createCharacter()` 在弹出输入框前调用 `confirmDiscardChanges()`；成功后使用：

```javascript
setCurrentDoc(payload, {
  id: payload.doc.id,
  display_name: payload.doc.display_name,
  source: "draft",
});
```

保存成功后更新 `request.characters` 并调用 `renderCharacterOptions()`，但传给 `studio.save_character` 的 `current_character_id` 继续使用 `request.initial_character_id || ""`。

- [ ] **Step 5: 更新加载和禁用状态**

`load()` 使用：

```javascript
async function load() {
  request = await invoke("load_request");
  applyTheme(request.theme || request.theme_defaults || {});
  const characters = Array.isArray(request.characters) ? request.characters : [];
  const initialId = characters.some((item) => item.id === request.initial_character_id)
    ? request.initial_character_id
    : (characters[0]?.id || "");
  editingCharacterId = initialId;
  renderCharacterOptions();
  if (initialId) {
    await openCharacter(initialId);
  } else {
    renderEditor();
    refreshControls();
  }
}
```

`refreshControls()` 增加：

```javascript
fields.studioCharacterSelect.disabled = busy || fields.studioCharacterSelect.options.length === 0;
fields.newCharacterButton.disabled = busy;
```

下拉事件使用：

```javascript
fields.studioCharacterSelect.addEventListener("change", (event) => selectCharacter(event.target.value));
```

- [ ] **Step 6: 添加角色工具栏样式**

```css
.studio-character-controls {
  display: grid;
  grid-template-columns: auto minmax(180px, 320px) auto;
  gap: 10px;
  align-items: center;
  margin-top: 12px;
}

.studio-character-controls > label {
  color: var(--sakura-secondary-text);
  font-size: 12.5px;
  font-weight: 600;
}

.studio-character-controls > select {
  width: 100%;
  min-width: 0;
}
```

删除 `.studio-toolbar`、`.character-list` 和 `.character-row*` 样式。

- [ ] **Step 7: 运行测试并提交**

Run: `.\runtime\python.exe -m pytest tests/ui/test_pet_window.py::test_tauri_studio_frontend_matches_settings_language tests/unit/test_tauri_studio.py -q`

Expected: PASS。

```powershell
git add tests/ui/test_pet_window.py tools/studio-tauri/frontend/index.html tools/studio-tauri/frontend/studio.js tools/studio-tauri/frontend/styles.css
git commit -m "fix: 改用下拉菜单切换编辑角色"
```

### Task 4: 对齐主设置配色编辑器

**Files:**
- Modify: `tests/ui/test_pet_window.py`
- Modify: `tools/studio-tauri/frontend/index.html`
- Modify: `tools/studio-tauri/frontend/studio.js`
- Modify: `tools/studio-tauri/frontend/styles.css`

- [ ] **Step 1: 写入失败的配色一致性测试**

扩展工作室前端测试：

```python
assert 'class="theme-colors"' in index
assert "themeLabels" not in source
assert "request.theme_fields.forEach(({ id, label })" in source
assert 'className = "theme-color-popover"' in source
assert 'hostCall("studio.pick_screen_color")' in source
assert "updateThemeFromRgbInputs" in source
assert "updateThemeFromSvPointer" in source
assert "updateThemeFromHuePointer" in source
assert ".theme-color-swatch" in styles
assert ".theme-color-popover" in styles
assert ".theme-sv-pad" in styles
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `.\runtime\python.exe -m pytest tests/ui/test_pet_window.py::test_tauri_studio_frontend_matches_settings_language -q`

Expected: FAIL，当前工作室只有 `theme-grid` 和简单文本输入。

- [ ] **Step 3: 改用主设置主题容器**

HTML 改为：

```html
<div id="themeFields" class="theme-colors"></div>
```

删除前端 `themeLabels`，增加 `themeEditor` 和 `activeThemeField` 状态。将主设置页以下纯前端函数原样迁入工作室，并仅把数据源固定为 `request.theme_fields` / `request.theme_defaults`：

```javascript
let activeThemeField = "";
let themeEditor = {};

function normalizeColorText(value, fallback) {
  const text = String(value || "").trim();
  const prefixed = text.startsWith("#") ? text : `#${text}`;
  return /^#[0-9a-fA-F]{6}$/.test(prefixed) ? prefixed.toLowerCase() : fallback;
}

function themeFieldInput(id) {
  return fields.themeFields.querySelector(`[data-theme-field="${id}"]`);
}

function themeFieldValue(id) {
  return normalizeColorText(themeFieldInput(id)?.value, request.theme_defaults[id]);
}

function applyTheme(theme) {
  request.theme_fields.forEach(({ id }) => {
    const color = normalizeColorText(theme[id], request.theme_defaults[id]);
    const cssVar = themeVars[id];
    if (cssVar && color) {
      document.documentElement.style.setProperty(cssVar, color);
    }
  });
}
```

`renderTheme(theme)` 必须为每个 `{id, label}` 创建 `.form-row.theme-color-row`、`.theme-color-swatch` 和带 `data-theme-field` 的 HEX 输入，并在容器末尾追加 `buildThemeEditor()`。迁入主设置页的完整颜色转换和编辑函数：

- `hexToRgb`、`componentToHex`、`rgbToHex`、`rgbToHsv`、`hsvToRgb`
- `buildThemeEditor`、`syncThemeRole`、`selectThemeField`、`syncThemeEditor`
- `openThemeColorPopover`、`closeThemeColorPopover`、`closeThemePopoverOnEscape`
- `updateActiveThemeColor`、`updateThemeFromRgbInputs`
- `updateThemeFromSvPointer`、`updateThemeFromHuePointer`

所有输入更新最终调用：

```javascript
function updateActiveThemeColor(color) {
  const normalized = normalizeColorText(color, "");
  const input = themeFieldInput(activeThemeField);
  if (!normalized || !input) {
    return;
  }
  input.value = normalized;
  document.documentElement.style.setProperty(themeVars[activeThemeField], normalized);
  syncThemeRole(activeThemeField);
  syncThemeEditor();
  refreshDirty();
}
```

- [ ] **Step 4: 接入工作室取色 RPC**

```javascript
async function pickActiveThemeColor() {
  if (!activeThemeField) {
    return;
  }
  themeEditor.pick.disabled = true;
  setError("");
  try {
    closeThemeColorPopover();
    const result = await hostCall("studio.pick_screen_color");
    if (result?.cancelled) {
      return;
    }
    const color = normalizeColorText(result?.color, "");
    if (!color) {
      throw new Error("取色结果无效。");
    }
    updateActiveThemeColor(color);
  } catch (error) {
    setError(`屏幕取色失败：${error}`);
  } finally {
    themeEditor.pick.disabled = false;
  }
}
```

- [ ] **Step 5: 完整重置角色主题**

`renderEditor()` 在创建主题控件前使用完整默认值合并角色主题：

```javascript
const theme = {
  ...(request.theme_defaults || {}),
  ...(doc.theme || {}),
};
applyTheme(theme);
renderTheme(theme);
```

这保证切换角色时全部 `--sakura-*` 变量都会被覆盖。

- [ ] **Step 6: 迁移主设置配色样式**

从 `tools/settings-tauri/frontend/styles.css` 精确迁移以下选择器及其响应式规则，不改颜色、尺寸或动效值：

- `.theme-colors`、`.theme-colors .form-row`
- `.form-row`、`.form-row > label:first-child`
- `.theme-color-control`、`.theme-color-control input[type="text"]`
- `.theme-color-swatch` 及 hover/active/invalid 状态
- `.theme-color-popover`、`::backdrop`
- `.theme-editor-head`、`.theme-editor-swatch`、`.theme-editor-title`
- `.theme-editor-field`、`.theme-rgb-row`
- `.theme-sv-pad`、`.theme-hue-strip`、picker pointer
- `.theme-editor-actions`

删除 `.theme-grid`、`.theme-field` 和 `.theme-swatch`。

- [ ] **Step 7: 运行测试并提交**

Run: `.\runtime\python.exe -m pytest tests/ui/test_pet_window.py::test_tauri_studio_frontend_matches_settings_language tests/unit/test_tauri_studio.py -q`

Expected: PASS。

```powershell
git add tests/ui/test_pet_window.py tools/studio-tauri/frontend/index.html tools/studio-tauri/frontend/studio.js tools/studio-tauri/frontend/styles.css
git commit -m "style: 对齐角色工作室配色控件"
```

### Task 5: 对齐导航、基础控件和响应式表现

**Files:**
- Modify: `tests/ui/test_pet_window.py`
- Modify: `tools/studio-tauri/frontend/index.html`
- Modify: `tools/studio-tauri/frontend/styles.css`

- [ ] **Step 1: 增加视觉契约断言**

```python
assert index.count("<svg") >= 4
assert 'aria-hidden="true"' in index
assert "grid-template-columns: 176px minmax(0, 1fr)" in styles
assert ".nav-item-icon" in styles
assert ".setting-row > select" in styles
assert ".secondary-button" in styles
assert "@media (max-width: 940px)" in styles
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `.\runtime\python.exe -m pytest tests/ui/test_pet_window.py::test_tauri_studio_frontend_matches_settings_language -q`

Expected: FAIL，因为工作室仍有独立的 190px 侧栏和不完整响应式规则。

- [ ] **Step 3: 对齐主设置结构样式**

将工作室以下基础选择器调整为主设置页对应定义：

- `.settings-shell` 使用 `176px minmax(0, 1fr)`。
- `.nav-list` 使用横向隐藏、纵向滚动。
- `.nav-item-icon` 使用 `display: grid; place-items: center; width: 18px; height: 18px; opacity: 0.82`。
- `.nav-item-label` 使用 `flex: 1` 和省略号。
- 输入、按钮、禁用、焦点状态与主设置页保持一致。
- `.setting-row` 和其右侧控件使用主设置页相同的宽度约束。
- 在 `@media (max-width: 940px)` 下收紧页边距和角色工具栏列宽，确保按钮及最长角色名不重叠。

- [ ] **Step 4: 运行前端契约测试并检查差异**

Run: `.\runtime\python.exe -m pytest tests/ui/test_pet_window.py::test_tauri_studio_frontend_matches_settings_language -q`

Expected: PASS。

Run: `git diff --check`

Expected: 无输出。

- [ ] **Step 5: 提交**

```powershell
git add tests/ui/test_pet_window.py tools/studio-tauri/frontend/index.html tools/studio-tauri/frontend/styles.css
git commit -m "style: 统一角色工作室界面元素"
```

### Task 6: 完整验证和 PR 准备

**Files:**
- Verify only

- [ ] **Step 1: 运行相关 Python 测试**

Run: `.\runtime\python.exe -m pytest tests/unit/test_tauri_studio.py tests/unit/test_character_studio.py tests/ui/test_pet_window.py -q`

Expected: PASS。

- [ ] **Step 2: 运行 Rust 测试和格式检查**

Run: `cargo fmt --manifest-path tools/studio-tauri/src-tauri/Cargo.toml --check`

Expected: PASS。

Run: `cargo test --manifest-path tools/studio-tauri/src-tauri/Cargo.toml`

Expected: PASS。

Run: `cargo test --manifest-path tools/settings-tauri/src-tauri/Cargo.toml`

Expected: PASS。

- [ ] **Step 3: 构建工作室并进行视觉验证**

Run: `cargo build --manifest-path tools/studio-tauri/src-tauri/Cargo.toml`

Expected: 生成可启动的 debug 工作室二进制。

通过主程序依次验证：

1. 设置页当前角色右侧只显示“修改角色”。
2. 点击后工作室下拉菜单显示该角色。
3. 切换角色、取消未保存提示、确认未保存提示均正确。
4. 新建角色作为临时选项显示，保存后转为正式选项。
5. 四个导航页图标、间距、选中状态与主设置一致。
6. 配色字段文案、顺序、色块、HEX、HSV/RGB 和屏幕取色工作正常。
7. 切换不同配色角色时无旧主题颜色残留。

- [ ] **Step 4: 运行完整测试**

Run: `.\runtime\python.exe -m pytest`

Expected: PASS。提 PR 前必须完成此步骤。

- [ ] **Step 5: 检查提交和工作区**

Run: `git status --short`

Expected: 只显示用户原有的 `install.bat`、`.superpowers/`、`_dbg/` 和 `link_sakura_runtime_tts.bat`，没有本任务未提交文件。

Run: `git log --oneline dev..HEAD`

Expected: 包含设计提交及本计划中的修复提交。

- [ ] **Step 6: 创建中文 PR**

```powershell
gh pr create --base dev --head fix/tauri-studio-ui --title "fix: 统一角色工作室交互与配色" --body "## 变更
- 合并设置页重复的角色工作室入口
- 使用持久下拉菜单选择当前编辑角色并支持新建角色
- 对齐主设置页导航、主题字段和完整配色编辑器
- 增加角色切换保护及主题/RPC 回归测试

## 验证
- .\\runtime\\python.exe -m pytest
- cargo test --manifest-path tools/studio-tauri/src-tauri/Cargo.toml
- cargo test --manifest-path tools/settings-tauri/src-tauri/Cargo.toml"
```
