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
        FRONTEND / "pet" / "layout.js",
        FRONTEND / "pet" / "portrait_controller.js",
        FRONTEND / "pet" / "subtitle_controller.js",
        FRONTEND / "pet" / "pet_controller.js",
    )

    assert all(path.is_file() for path in required)
    assert 'type="module"' in (FRONTEND / "index.html").read_text(encoding="utf-8")
    app_source = (FRONTEND / "app.js").read_text(encoding="utf-8")
    assert 'from "./core/store.js"' in app_source
    assert "createPetStore" not in app_source


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
