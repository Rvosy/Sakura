from __future__ import annotations

import json
from pathlib import Path

from app.ui.control_panel_layout import compute_pet_layout


ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "desktop" / "frontend"


def _module_url(relative: str) -> str:
    return (ROOT / relative).resolve().as_uri()


def test_tauri_pet_frontend_has_single_store_and_feature_modules() -> None:
    required = (
        FRONTEND / "core" / "store.js",
        FRONTEND / "core" / "theme.js",
        FRONTEND / "core" / "bootstrap_loader.js",
        FRONTEND / "pet" / "layout.js",
        FRONTEND / "pet" / "portrait_controller.js",
        FRONTEND / "pet" / "subtitle_controller.js",
        FRONTEND / "pet" / "pet_controller.js",
        FRONTEND / "pet" / "context_menu.js",
    )

    assert all(path.is_file() for path in required)
    assert 'type="module"' in (FRONTEND / "index.html").read_text(encoding="utf-8")
    app_source = (FRONTEND / "app.js").read_text(encoding="utf-8")
    assert 'from "./core/store.js"' in app_source
    assert "createPetStore" not in app_source


def test_bootstrap_loader_coalesces_repeated_ready_status_events(
    node_module_runner,
) -> None:  # type: ignore[no-untyped-def]
    payload = node_module_runner(
        f"""
import {{ createSessionBootstrapLoader }} from {json.dumps(_module_url('desktop/frontend/core/bootstrap_loader.js'))};
let resolveFirst;
let resolveSecond;
let fetchCount = 0;
const applied = [];
const loader = createSessionBootstrapLoader({{
  fetchBootstrap: () => {{
    fetchCount += 1;
    return new Promise((resolve) => {{
      if (fetchCount === 1) resolveFirst = resolve;
      else resolveSecond = resolve;
    }});
  }},
  applyBootstrap: (bootstrap) => applied.push(bootstrap.character.id),
}});
const firstBrain = {{ acceptingRequests: true, sessionGeneration: 1 }};
const firstLoads = Array.from({{ length: 25 }}, () => loader.load(firstBrain));
await Promise.resolve();
const countDuringStatusStorm = fetchCount;
resolveFirst({{ character: {{ id: "first" }} }});
await Promise.all(firstLoads);
await loader.load(firstBrain);
loader.reset();
const secondBrain = {{ acceptingRequests: true, sessionGeneration: 2 }};
const secondLoads = [loader.load(secondBrain), loader.load(secondBrain)];
await Promise.resolve();
resolveSecond({{ character: {{ id: "second" }} }});
await Promise.all(secondLoads);
console.log(JSON.stringify({{ fetchCount, countDuringStatusStorm, applied }}));
"""
    )

    assert payload == {
        "fetchCount": 2,
        "countDuringStatusStorm": 1,
        "applied": ["first", "second"],
    }


def test_store_and_theme_mapping_are_deterministic(
    node_module_runner,
) -> None:  # type: ignore[no-untyped-def]
    payload = node_module_runner(
        f"""
import {{ createPetStore }} from {json.dumps(_module_url('desktop/frontend/core/store.js'))};
import {{ themeToCssVariables }} from {json.dumps(_module_url('desktop/frontend/core/theme.js'))};
const store = createPetStore();
const snapshots = [];
store.subscribe((state) => snapshots.push([state.character?.id ?? null, state.interaction.busy]));
store.setBootstrap({{ character: {{ id: "demo" }}, theme: {{ primary_color: "#123456" }} }});
store.setInteractionState({{ busy: true, interactionId: "interaction-1" }});
console.log(JSON.stringify({{
  state: store.getState(),
  snapshots,
  variables: themeToCssVariables({{
    primary_color: "#123456",
    bubble_background_color: "#abcdef",
    text_color: "#112233",
  }}),
}}));
"""
    )

    assert payload["state"]["character"] == {"id": "demo"}  # type: ignore[index]
    assert payload["state"]["interaction"]["busy"] is True  # type: ignore[index]
    assert payload["snapshots"] == [["demo", False], ["demo", True]]
    assert payload["variables"] == {
        "--sakura-primary": "#123456",
        "--sakura-bubble": "#abcdef",
        "--sakura-text": "#112233",
    }


def test_js_layout_matches_existing_python_layout_model(
    node_module_runner,
) -> None:  # type: ignore[no-untyped-def]
    cases = [
        {
            "portraitWidth": 560,
            "portraitHeight": 570,
            "controlPanelWidth": 640,
            "bubbleHeight": 128,
            "verticalOffset": 0,
            "inputBarOffset": 0,
        },
        {
            "portraitWidth": 420,
            "portraitHeight": 510,
            "controlPanelWidth": 520,
            "bubbleHeight": 220,
            "verticalOffset": 90,
            "inputBarOffset": 120,
        },
    ]
    js_layouts = node_module_runner(
        f"""
import {{ computePetLayout }} from {json.dumps(_module_url('desktop/frontend/pet/layout.js'))};
const cases = {json.dumps(cases)};
console.log(JSON.stringify({{ layouts: cases.map(computePetLayout) }}));
"""
    )["layouts"]

    for case, js_layout in zip(cases, js_layouts, strict=True):  # type: ignore[arg-type]
        python_layout = compute_pet_layout(
            portrait_width=case["portraitWidth"],
            portrait_height=case["portraitHeight"],
            control_panel_width=case["controlPanelWidth"],
            bubble_height=case["bubbleHeight"],
            vertical_offset=case["verticalOffset"],
            input_bar_offset=case["inputBarOffset"],
        )
        assert js_layout == {  # type: ignore[comparison-overlap]
            "windowSize": list(python_layout.window_size),
            "portraitRect": list(python_layout.portrait_rect),
            "bubbleRect": list(python_layout.bubble_rect),
            "inputRect": list(python_layout.input_rect),
            "portraitAnchor": list(python_layout.portrait_anchor),
        }


def test_subtitle_controller_segments_and_ignores_late_cancelled_callbacks(
    node_module_runner,
) -> None:  # type: ignore[no-untyped-def]
    payload = node_module_runner(
        f"""
import {{ SubtitleController }} from {json.dumps(_module_url('desktop/frontend/pet/subtitle_controller.js'))};
const target = {{ textContent: "" }};
const queue = [];
const seen = [];
let completed = 0;
const controller = new SubtitleController({{
  target,
  language: "zh",
  typingIntervalMs: 1,
  segmentPauseMs: 0,
  setTimer: (callback) => (queue.push(callback), callback),
  clearTimer: () => {{}},
  onSegment: (segment) => seen.push([segment.tone, segment.portrait]),
  onComplete: () => completed += 1,
}});
controller.showSegments([
  {{ ja: "一", zh: "第一段", tone: "calm", portrait: "smile" }},
  {{ ja: "二", zh: "第二段", tone: "happy", portrait: "laugh" }},
]);
while (queue.length) queue.shift()();
controller.showSegments([{{ ja: "遅い", zh: "迟到结果" }}]);
const late = queue.shift();
controller.cancel("初始消息");
late();
console.log(JSON.stringify({{ text: target.textContent, seen, completed }}));
"""
    )

    assert payload == {
        "text": "初始消息",
        "seen": [["calm", "smile"], ["happy", "laugh"], [None, None]],
        "completed": 1,
    }


def test_portrait_controller_maps_expression_and_finishes_crossfade(
    node_module_runner,
) -> None:  # type: ignore[no-untyped-def]
    payload = node_module_runner(
        f"""
class ClassList {{
  constructor() {{ this.values = new Set(); }}
  add(value) {{ this.values.add(value); }}
  remove(value) {{ this.values.delete(value); }}
  has(value) {{ return this.values.has(value); }}
}}
class FakeImage {{
  constructor() {{
    this.complete = true;
    this.naturalWidth = 800;
    this.naturalHeight = 1000;
    this.classList = new ClassList();
    this.attributes = new Map();
    this._src = "";
  }}
  set src(value) {{ this._src = value; this.attributes.set("src", value); }}
  get src() {{ return this._src; }}
  getAttribute(name) {{ return this.attributes.get(name) || null; }}
  removeAttribute(name) {{ this.attributes.delete(name); if (name === "src") this._src = ""; }}
  addEventListener(name, callback) {{ if (name === "load") callback(); }}
}}
globalThis.Image = FakeImage;
globalThis.window = {{ setTimeout: (callback) => (callback(), 1) }};
const {{ PortraitController }} = await import({json.dumps(_module_url('desktop/frontend/pet/portrait_controller.js'))});
const current = new FakeImage();
const transition = new FakeImage();
const fallback = {{ hidden: false, dataset: {{}} }};
const sizes = [];
const controller = new PortraitController({{
  currentImage: current,
  transitionImage: transition,
  fallback,
  onNaturalSize: (size) => sizes.push(size),
}});
controller.setCharacter({{
  portraits: {{ default: "asset-default", expressions: {{ smile: "asset-smile" }} }},
}});
controller.showForSegment({{ tone: "smile" }});
console.log(JSON.stringify({{
  source: current.src,
  transitionSource: transition.src,
  fallbackHidden: fallback.hidden,
  currentKey: controller.currentKey,
  sizes,
}}));
"""
    )

    assert payload["source"] == "asset-smile"
    assert payload["transitionSource"] == ""
    assert payload["fallbackHidden"] is True
    assert payload["currentKey"] == "smile"
    assert payload["sizes"][-1] == {"width": 800, "height": 1000}  # type: ignore[index]


def test_pet_controller_replaces_character_theme_and_layout_in_same_session(
    node_module_runner,
) -> None:  # type: ignore[no-untyped-def]
    payload = node_module_runner(
        f"""
class FakeElement {{
  constructor() {{
    this.disabled = false;
    this.hidden = false;
    this.placeholder = "";
    this.textContent = "";
    this.value = "";
    this.listeners = new Map();
  }}
  addEventListener(name, callback) {{ this.listeners.set(name, callback); }}
}}
const styles = {{}};
globalThis.document = {{
  documentElement: {{
    dataset: {{}},
    style: {{ setProperty: (name, value) => styles[name] = value }},
  }},
}};
globalThis.window = {{ dispatchEvent: () => {{}} }};
globalThis.CustomEvent = class {{ constructor(name, options) {{ this.name = name; this.detail = options.detail; }} }};
const {{ createPetStore }} = await import({json.dumps(_module_url('desktop/frontend/core/store.js'))});
const {{ PetController }} = await import({json.dumps(_module_url('desktop/frontend/pet/pet_controller.js'))});
const elements = {{
  characterName: new FakeElement(),
  input: new FakeElement(),
  send: new FakeElement(),
  cancel: new FakeElement(),
  capture: new FakeElement(),
  openSettingsButton: new FakeElement(),
  openHistoryButton: new FakeElement(),
}};
const layouts = [];
const portraits = [];
const subtitleConfigs = [];
const subtitleTexts = [];
const store = createPetStore();
const controller = new PetController({{
  store,
  invoke: async (command, args) => layouts.push([command, args]),
  portraitController: {{ setCharacter: (character) => portraits.push(character.id) }},
  subtitleController: {{
    configure: (subtitle) => subtitleConfigs.push(subtitle.language),
    setText: (value) => subtitleTexts.push(value),
  }},
  elements,
}});
controller.applyBootstrap({{
  sessionGeneration: 1,
  character: {{ id: "first", displayName: "First", initialMessage: "first hello" }},
  theme: {{ primary_color: "#111111", visual_effect_mode: "solid" }},
  layout: {{ portrait_scale_percent: 100, control_panel_width: 640, bubble_height: 128 }},
  subtitle: {{ language: "zh" }},
}});
controller.applyBootstrap({{
  sessionGeneration: 2,
  character: {{ id: "second", displayName: "Second", initialMessage: "second hello" }},
  theme: {{ primary_color: "#222222", visual_effect_mode: "gaussian_blur" }},
  layout: {{ portrait_scale_percent: 120, control_panel_width: 700, bubble_height: 160 }},
  subtitle: {{ language: "ja" }},
}});
console.log(JSON.stringify({{
  character: store.getState().character,
  characterName: elements.characterName.textContent,
  styles,
  visualEffect: document.documentElement.dataset.visualEffect,
  portraits,
  subtitleConfigs,
  subtitleTexts,
  layouts,
}}));
"""
    )

    assert payload["character"]["id"] == "second"  # type: ignore[index]
    assert payload["characterName"] == "Second"
    assert payload["styles"]["--sakura-primary"] == "#222222"  # type: ignore[index]
    assert payload["visualEffect"] == "gaussian_blur"
    assert payload["portraits"] == ["first", "second"]
    assert payload["subtitleConfigs"] == ["zh", "ja"]
    assert payload["subtitleTexts"] == ["first hello", "second hello"]
    assert len(payload["layouts"]) == 2  # type: ignore[arg-type]


def test_pet_markup_has_portrait_subtitle_input_cancel_and_screenshot_controls() -> None:
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    app_source = (FRONTEND / "app.js").read_text(encoding="utf-8")
    pet_source = (FRONTEND / "pet" / "pet_controller.js").read_text(encoding="utf-8")
    rust_state = (ROOT / "desktop" / "src-tauri" / "src" / "app_state.rs").read_text(
        encoding="utf-8"
    )

    for element_id in (
        "portrait-current",
        "portrait-transition",
        "character-name",
        "subtitle-text",
        "message-input",
        "send-message",
        "cancel-message",
        "capture-screen",
    ):
        assert f'id="{element_id}"' in html
    assert "compositionstart" in pet_source
    assert "compositionend" in pet_source
    assert "convertFileSrc" not in app_source
    assert "file://" not in app_source
    assert "sakura-asset" in rust_state
    assert "canonicalize" in rust_state
    assert "strip_prefix" in rust_state or "starts_with" in rust_state


def test_pet_bubble_drag_region_and_custom_context_menu_contract() -> None:
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    app_source = (FRONTEND / "app.js").read_text(encoding="utf-8")
    menu_source = (FRONTEND / "pet" / "context_menu.js").read_text(encoding="utf-8")
    styles = (FRONTEND / "styles.css").read_text(encoding="utf-8")
    rust_state = (ROOT / "desktop" / "src-tauri" / "src" / "app_state.rs").read_text(
        encoding="utf-8"
    )
    rust_actions = (
        ROOT / "desktop" / "src-tauri" / "src" / "menu_actions.rs"
    ).read_text(encoding="utf-8")

    speech_markup = html[html.index('id="speech-bubble"') : html.index('id="tool-confirmation"')]
    assert "data-drag-region" in speech_markup
    assert "data-tauri-drag-region" in speech_markup
    assert 'id="character-name"' in speech_markup
    assert 'id="subtitle-text"' in speech_markup

    assert 'id="pet-context-menu"' in html
    assert 'role="menu"' in html
    assert html.count('role="menuitemcheckbox"') == 3
    assert 'aria-checked="false"' in html
    for action in (
        "hide",
        "subtitle",
        "free-access",
        "always-on-top",
        "history",
        "diagnostics",
        "settings",
        "quit",
    ):
        assert f'data-menu-action="{action}"' in html

    assert 'from "./pet/context_menu.js"' in app_source
    assert 'invoke("set_pet_subtitle_language"' in menu_source
    assert 'invoke("set_pet_free_access"' in menu_source
    assert 'invoke("set_pet_always_on_top"' in menu_source
    assert 'invoke("pet_menu_action"' in menu_source
    assert "event.clientX" in menu_source and "event.clientY" in menu_source
    assert 'event.key !== "Escape"' in menu_source
    assert 'this.window.addEventListener("blur"' in menu_source
    for excluded in ("button", "input", "details", "#input-card", "#tool-confirmation"):
        assert f'"{excluded}"' in menu_source
    assert "set_pet_always_on_top" in rust_state
    assert "apply_reversible_always_on_top" in rust_state
    assert "request_application_exit" in rust_actions

    for token in (
        "border-radius: 14px",
        "padding: 6px",
        "padding: 5px 20px 5px 24px",
        "border-radius: 8px",
        "var(--sakura-input)",
        "var(--sakura-border)",
        "var(--sakura-panel)",
        "backdrop-filter",
        ".pet-context-menu__separator",
        '[aria-checked="true"]',
        'button[aria-disabled="true"]',
    ):
        assert token in styles


def test_context_menu_position_is_clamped_to_webview_viewport(node_module_runner) -> None:  # type: ignore[no-untyped-def]
    payload = node_module_runner(
        f"""
import {{ clampMenuPosition }} from {json.dumps(_module_url('desktop/frontend/pet/context_menu.js'))};
console.log(JSON.stringify({{
  bottomRight: clampMenuPosition(735, 639, 226, 300, {{ width: 736, height: 640 }}),
  topLeft: clampMenuPosition(-10, -20, 226, 300, {{ width: 736, height: 640 }}),
}}));
"""
    )

    assert payload == {
        "bottomRight": {"x": 502, "y": 332},
        "topLeft": {"x": 8, "y": 8},
    }
