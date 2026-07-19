# WP-1A-03 Hit, Drag, and Focus Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove that the fixed 816×680 single transparent Tauri/WebView window supports transparent click-through, bounded drag, real text/IME input, and deterministic focus recovery without starting Python or changing user data.

**Architecture:** Extend the existing shared layout contract with one explicit control rectangle per presentation state. A small DOM-free frontend module derives ordered `interactive`, `drag`, and `neutral` regions, while a matching Rust module derives and scales the same model, applies its union as the native HWND region, and restores the full HWND region on platform failure. Drag completion is reconciled through the existing monitor/DPI geometry contract so the physical portrait anchor remains stable across later state changes.

**Tech Stack:** Vanilla ES modules and Node `node:test`; Rust 2021; Tauri 2.11.3; `windows` 0.61.3 Win32 GDI/window APIs; PowerShell UI Automation acceptance on Windows 11/WebView2.

## Global Constraints

- Work only on `refactor/tauri-runtime-v2`; do not create a branch or PR.
- Preserve the fixed 816×680 logical native envelope and viewport portrait anchor `(480, 668)`.
- Do not modify `main.py`, `start.bat`, `app/`, `plugins/`, `data/`, `runtime/`, `characters/`, `third_party/`, `tools/mcp/`, or `data/runtime_v2/`.
- Do not start Python, add chat/provider/character logic, add another native window, or begin WP-1A-04.
- Keep the platform implementation Windows-specific and minimal; no reusable window framework.
- Every production behavior starts with an executable failing test.

---

### Task 1: Shared deterministic hit-region contract

**Files:**
- Modify: `desktop/frontend/pet/layout-contract.json`
- Create: `desktop/frontend/pet/hit-regions.js`
- Create: `desktop/frontend/tests/hit-regions.test.js`
- Modify: `desktop/frontend/pet/layout.js`

**Interfaces:**
- Consumes: `computePetLayout(contract, state)` and its viewport-relative portrait, bubble, input, and control rectangles.
- Produces: `computeHitRegions(layout)`, `classifyHitPoint(model, point)`, and ordered `{ interactive, drag, neutral }` rectangles; transparent is the complement of their union.

- [x] Write tests covering all four states, exact transparent/edge classification, interactive-over-drag priority, controls/input never classifying as drag, invalid rectangles, and deterministic rapid state computation.
- [x] Run `node --test desktop/frontend/tests/hit-regions.test.js` and confirm failure because the module/contract is missing.
- [x] Add `controlsRect` to every shared state layout, translate it into viewport coordinates, and implement the minimal pure hit model and classifier.
- [x] Run the focused test and all `node --test desktop/frontend/tests/*.test.js`; confirm green.

### Task 2: Rust hit geometry and safe native region application

**Files:**
- Create: `desktop/src-tauri/src/window_interaction.rs`
- Modify: `desktop/src-tauri/src/window_geometry.rs`
- Modify: `desktop/src-tauri/src/main.rs`
- Modify: `desktop/src-tauri/Cargo.toml`

**Interfaces:**
- Consumes: `LayoutContract`, `PresentationState`, `LayoutApplication.content_scale`, and monitor scale factor.
- Produces: `logical_hit_regions`, `scale_hit_regions`, `apply_native_hit_regions`, `restore_full_native_hit_region`, and a serializable hit model returned with each accepted layout revision.

- [x] Add Rust tests for the four shared layouts, boundary and priority rules, 100/125/150% scaling, extreme coordinates, stale revisions, and safe-recovery decision behavior.
- [x] Run `cargo test --manifest-path desktop/src-tauri/Cargo.toml --locked` and confirm the new tests fail because the interaction module is absent.
- [x] Implement shared-contract parsing and Win32 `SetWindowRgn` union application; on any region error clear the HWND region so the whole window remains interactive and closable.
- [x] Apply native bounds and hit regions under the same accepted layout revision before showing/committing the DOM state.
- [x] Run focused Rust tests, then the full locked Rust test suite; confirm green.

### Task 3: Drag reconciliation and anchor preservation

**Files:**
- Modify: `desktop/src-tauri/src/window_interaction.rs`
- Modify: `desktop/src-tauri/src/window_geometry.rs`
- Modify: `desktop/src-tauri/src/main.rs`

**Interfaces:**
- Consumes: current HWND physical position, target monitor work area/scale factor, current presentation state, and the fixed viewport anchor.
- Produces: `start_pet_drag` returning the reconciled `LayoutApplication` after native dragging and updating the session portrait anchor.

- [x] Add tests proving dragged physical anchors on single/multi/negative-coordinate monitors, 100/125/150% DPI, work-area edges, undersized work areas, and later four-state transitions.
- [x] Run the focused Rust tests and confirm expected failures for missing drag reconciliation.
- [x] Implement primary drag through the bounded Win32 move loop, recompute the anchor from the post-drag HWND, select the target monitor, reapply work-area correction and native hit region, then store the normalized anchor.
- [x] Run all locked Rust tests and confirm green.

### Task 4: Real composer, IME guard, and focus recovery

**Files:**
- Create: `desktop/frontend/pet/input-focus.js`
- Create: `desktop/frontend/tests/input-focus.test.js`
- Modify: `desktop/frontend/index.html`
- Modify: `desktop/frontend/styles.css`
- Modify: `desktop/frontend/app.js`

**Interfaces:**
- Produces: `createInputFocusController({ focusInput, localSubmit })` with composition start/update/end, focus/blur, visibility, presentation-state, Enter-key, and local button-submit transitions.
- The controller never emits chat/IPC requests; submit only updates local technical feedback.

- [x] Write pure state-machine tests for English input, composition updates, Enter suppression while composing, focus/blur/Alt+Tab recovery, hide/show recovery, state round-trips, and no drag initiation from input/buttons/controls.
- [x] Run `node --test desktop/frontend/tests/input-focus.test.js` and confirm the missing controller fails.
- [x] Replace the composer placeholder with a real textarea and local send button, wire composition/focus events, restore focus only when composer/expanded is active, and wire portrait/bubble drag while preserving interactive priority.
- [x] Run all frontend tests and confirm green.

### Task 5: Stabilization, real Windows evidence, and acceptance record

**Files:**
- Create or modify: `desktop/tests/windows_pet_interaction_acceptance.ps1`
- Modify: `desktop/tests/windows_pet_geometry_acceptance.ps1` only if shared helpers are required
- Modify: `docs/superpowers/plans/2026-07-15-runtime-v2-work-packages.md`

**Interfaces:**
- The acceptance script records debug/release window state, physical hit probes, drag and anchor results, input/focus/visibility behavior, process tree, Python absence, and before/after data manifests without modifying real data.

- [x] Mark WP-1A-03 `stabilizing` immediately after production implementation.
- [x] Run formatting, frontend tests, Rust tests, debug/release locked builds, PowerShell syntax checks, and `git diff --check`.
- [x] Run debug and release real-window acceptance for click-through, interaction, portrait/bubble drag, non-drag controls, English/Chinese IME, Alt+Tab, hide/show, state round-trips, no white flash/layout drift, no Python, no data change, and no process residue.
- [x] Record unavailable physical multi-monitor/negative-coordinate/125%/150% DPI evidence separately from deterministic automated coverage.
- [x] If any stop condition remains, keep `stabilizing`; otherwise record zero P0/P1 and mark WP-1A-03 `accepted`.
- [x] Review the final diff and commit with `feat(runtime): 建立透明窗口命中、拖动与输入焦点技术门` and the required evidence-rich body.
