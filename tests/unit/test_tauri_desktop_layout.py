from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DESKTOP = ROOT / "desktop"
FRONTEND = DESKTOP / "frontend"
TAURI = DESKTOP / "src-tauri"


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_desktop_crate_and_vanilla_frontend_exist() -> None:
    required = (
        FRONTEND / "index.html",
        FRONTEND / "app.js",
        FRONTEND / "styles.css",
        FRONTEND / "pet" / "context_menu.js",
        TAURI / "Cargo.toml",
        TAURI / "build.rs",
        TAURI / "tauri.conf.json",
        TAURI / "capabilities" / "default.json",
        TAURI / "src" / "main.rs",
        TAURI / "src" / "lib.rs",
        TAURI / "src" / "app_state.rs",
        TAURI / "src" / "brain_host.rs",
        TAURI / "src" / "windows.rs",
        TAURI / "src" / "tray.rs",
        TAURI / "src" / "audio.rs",
        TAURI / "src" / "capture.rs",
        TAURI / "src" / "menu_actions.rs",
    )

    assert all(path.is_file() for path in required)
    assert not (FRONTEND / "package.json").exists()


def test_main_window_is_a_transparent_desktop_pet_surface() -> None:
    config = json.loads(_read("desktop/src-tauri/tauri.conf.json"))
    window = config["app"]["windows"][0]

    assert config["build"]["frontendDist"] == "../frontend"
    assert config["app"]["withGlobalTauri"] is True
    assert window["label"] == "main"
    assert window["transparent"] is True
    assert window["decorations"] is False
    assert window["alwaysOnTop"] is False
    assert window["shadow"] is False
    assert window["resizable"] is False
    assert window["skipTaskbar"] is True
    assert (window["width"], window["height"]) == (736, 640)
    assert window["visible"] is False


def test_desktop_does_not_expose_shell_or_arbitrary_filesystem_plugins() -> None:
    cargo = _read("desktop/src-tauri/Cargo.toml").lower()
    capability = _read("desktop/src-tauri/capabilities/default.json").lower()
    rust = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((TAURI / "src").glob("*.rs"))
    ).lower()

    assert 'name = "sakura-desktop"' in cargo
    assert "tauri-plugin-shell" not in cargo
    assert "tauri-plugin-fs" not in cargo
    assert "shell:" not in capability
    assert "fs:" not in capability
    assert "std::process::command" not in rust


def test_frontend_has_drag_visibility_click_through_ime_and_prototype_controls() -> None:
    html = _read("desktop/frontend/index.html")
    script = _read("desktop/frontend/app.js")
    styles = _read("desktop/frontend/styles.css")
    pet_controller = _read("desktop/frontend/pet/pet_controller.js")

    assert 'id="pet-stage"' in html
    assert 'id="message-input"' in html
    assert 'id="audio-prototype"' in html
    assert 'id="capture-prototype"' in html
    assert 'id="open-studio-empty"' not in html
    assert "data-tauri-drag-region" in html
    assert "compositionstart" in pet_controller
    assert "compositionend" in pet_controller
    assert "start_dragging" in script
    assert "set_pet_visible" in script
    assert "set_click_through" in script
    assert "play_audio_prototype" in script
    assert "capture_screen_prototype" in script
    assert ".portrait-fallback[hidden]" in styles


def test_main_pet_uses_custom_context_menu_while_secondary_frontends_disable_browser_menu() -> None:
    main_script = _read("desktop/frontend/app.js")
    context_menu = _read("desktop/frontend/pet/context_menu.js")

    assert 'from "./pet/context_menu.js"' in main_script
    assert 'this.document.addEventListener("contextmenu"' in context_menu
    assert "event.preventDefault()" in context_menu
    assert "openAt(event.clientX, event.clientY)" in context_menu

    secondary_entrypoints = (
        "desktop/frontend/capture/capture_selection.js",
        "desktop/frontend/diagnostics/diagnostics.js",
        "desktop/frontend/history/history.js",
        "desktop/frontend/runtime-repair/runtime_repair.js",
        "desktop/frontend/settings/settings.js",
        "desktop/frontend/studio/studio.js",
    )

    for entrypoint in secondary_entrypoints:
        script = _read(entrypoint)
        assert 'document.addEventListener("contextmenu", (event) => event.preventDefault());' in script


def test_tauri_has_onboarding_gate_and_distinct_runtime_repair_surface() -> None:
    rust_state = _read("desktop/src-tauri/src/app_state.rs")
    rust_windows = _read("desktop/src-tauri/src/windows.rs")
    rust_entry = _read("desktop/src-tauri/src/lib.rs")
    tray = _read("desktop/src-tauri/src/tray.rs")
    settings_html = _read("desktop/frontend/settings/index.html")
    settings_script = _read("desktop/frontend/settings/settings.js")
    capability = json.loads(_read("desktop/src-tauri/capabilities/default.json"))

    assert "onboarding_required" in rust_state
    assert "runtime_repair" in rust_state
    assert "bootstrap_status" in rust_entry
    assert "show_application_window" in rust_entry
    assert "menu_actions::dispatch" in tray
    assert "PetMenuAction::Show" in tray
    assert "/settings/index.html" in rust_windows
    assert "/runtime-repair/index.html" in rust_windows
    assert "runtime-repair" in capability["windows"]
    for control in (
        'id="characterSelect"',
        'id="characterImportButton"',
        'id="characterEditorButton"',
        'id="providerList"',
        'id="modelSlots"',
    ):
        assert control in settings_html
    assert 'hostCall("character.import_archive"' in settings_script
    assert 'hostCall("studio.launch"' in settings_script
    assert 'listen?.("sakura://character-changed"' in settings_script
    assert (FRONTEND / "runtime-repair" / "index.html").is_file()
    assert (FRONTEND / "runtime-repair" / "runtime_repair.js").is_file()
    assert (FRONTEND / "runtime-repair" / "styles.css").is_file()


def test_csp_disallows_remote_scripts_and_object_embedding() -> None:
    config = json.loads(_read("desktop/src-tauri/tauri.conf.json"))
    csp = config["app"]["security"]["csp"]

    assert "script-src 'self'" in csp
    assert "object-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp


def test_brain_host_supervisor_is_owned_by_tauri_and_exposes_diagnostic_state() -> None:
    app_state = _read("desktop/src-tauri/src/app_state.rs")
    brain_host = _read("desktop/src-tauri/src/brain_host.rs")
    rust_entry = _read("desktop/src-tauri/src/lib.rs")
    frontend = _read("desktop/frontend/app.js")

    assert "BrainHostSupervisor" in app_state
    assert "BrainHostLaunchConfig::for_current_app" in app_state
    assert "BRAIN_RESTART_LIMIT" in brain_host
    assert "max_restarts" in brain_host
    assert "ExitRequested" in rust_entry
    assert "brain_status" in rust_entry
    assert "sakura://brain-status" in frontend
    assert 'phase === "diagnostic"' in frontend
