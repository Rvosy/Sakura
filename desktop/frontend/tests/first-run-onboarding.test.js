import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const html = await readFile(new URL("../onboarding/index.html", import.meta.url), "utf8");
const source = await readFile(new URL("../onboarding/onboarding.js", import.meta.url), "utf8");
const styles = await readFile(new URL("../onboarding/styles.css", import.meta.url), "utf8");
const rustMain = await readFile(new URL("../../src-tauri/src/main.rs", import.meta.url), "utf8");
const legacyImportRust = await readFile(new URL("../../src-tauri/src/legacy_import.rs", import.meta.url), "utf8");

test("welcome page exposes exactly the two requested primary routes", () => {
  assert.match(html, /id="firstUseButton"[\s\S]*?>\s*初次使用\s*</);
  assert.match(html, /id="migrationButton"[\s\S]*?>\s*迁移旧版本\s*</);
  assert.equal((html.match(/class="route-card(?:\s|\")/g) || []).length, 2);
  assert.doesNotMatch(html, /route-head|route-label|route-card-copy|route-stem|<small>/);
});

test("migration route selects, inspects, starts and can cancel an explicit import", () => {
  assert.match(html, /id="migrationChooseButton"[\s\S]*?>选择旧版目录</);
  assert.match(html, /id="migrationBackButton"[\s\S]*?>返回</);
  assert.match(html, /id="migrationStartButton"[\s\S]*?>开始迁移</);
  assert.match(html, /id="migrationCancelButton"[\s\S]*?>取消迁移</);
  assert.match(source, /invoke\("legacy_import_choose_source"\)/);
  assert.match(source, /invoke\("legacy_import_inspect", \{ selectionId: snapshot\.selectionId \}\)/);
  assert.match(source, /invoke\("legacy_import_start", \{[\s\S]*?selectionId,[\s\S]*?confirmedOverwriteDomains/);
  assert.match(source, /window\.confirm\([\s\S]*?存在以下冲突[\s\S]*?目标独有内容会保留/);
  assert.match(source, /invoke\("legacy_import_cancel"\)/);
  assert.doesNotMatch(source, /sourcePath|legacyPath|migrationPath/);
  assert.match(source, /诊断日志：\$\{error\.diagnosticLog\}/);
});

test("migration route explains supported and cross-platform legacy sources", () => {
  assert.match(source, /LEGACY_PLATFORM_UNSUPPORTED/);
  assert.match(source, /LEGACY_TARGET_PLATFORM_UNSUPPORTED/);
  assert.match(source, /LEGACY_CROSS_PLATFORM_UNSUPPORTED/);
  assert.match(source, /LEGACY_TTS_ABSOLUTE_LINKS_SKIPPED/);
  assert.match(source, /Windows 或 macOS 安装目录/);
});

test("choosing keeps the path opaque and inspection freezes overwrite domains before start", () => {
  const chooseStart = legacyImportRust.indexOf("pub fn legacy_import_choose_source(");
  const chooseEnd = legacyImportRust.indexOf("pub async fn legacy_import_inspect(", chooseStart);
  const inspectStart = chooseEnd;
  const inspectEnd = legacyImportRust.indexOf("pub fn legacy_import_state(", inspectStart);
  const startStart = legacyImportRust.indexOf("pub fn legacy_import_start(");
  const startEnd = legacyImportRust.indexOf("pub fn legacy_import_cancel(", startStart);
  assert.notEqual(chooseStart, -1);
  assert.notEqual(chooseEnd, -1);
  assert.notEqual(inspectEnd, -1);
  assert.notEqual(startStart, -1);
  assert.notEqual(startEnd, -1);
  assert.doesNotMatch(legacyImportRust.slice(chooseStart, chooseEnd), /run_python|stream_run|thread::spawn/);
  assert.match(legacyImportRust.slice(chooseStart, chooseEnd), /state: "selected"\.to_string\(\)/);
  assert.match(legacyImportRust.slice(inspectStart, inspectEnd), /run_python\([\s\S]*?"inspect"[\s\S]*?overwriteDomains/);
  assert.match(legacyImportRust.slice(startStart, startEnd), /state != "ready"[\s\S]*?overwrite_domains != confirmed_overwrite_domains[\s\S]*?state = "staging"\.to_string\(\)[\s\S]*percent = 1[\s\S]*thread::spawn/);
  assert.match(source, /const activeMigrationStates = new Set\(\[[\s\S]*?"inspecting"[\s\S]*?"staging"/);
});

test("migration progress rejects stale snapshots and polls backend state as a fallback", () => {
  assert.match(source, /isProgressRegression\(previous, state, percent\)/);
  assert.match(source, /return percent < previous\.percent/);
  assert.match(source, /renderProgress\(await invoke\("legacy_import_state"\)\)/);
  assert.match(source, /syncMigrationPolling\(active\)/);
  assert.match(source, /}, 1000\)/);
  const publishStart = legacyImportRust.indexOf("fn publish(");
  const publishEnd = legacyImportRust.indexOf("#[tauri::command]", publishStart);
  assert.match(legacyImportRust.slice(publishStart, publishEnd), /run_on_main_thread/);
  assert.match(legacyImportRust.slice(publishStart, publishEnd), /emit_to/);
  assert.doesNotMatch(legacyImportRust.slice(publishStart, publishEnd), /app\.emit\(LEGACY_IMPORT_PROGRESS_EVENT/);
});

test("new-user route starts Core while migration completes only after backend validation", () => {
  assert.match(source, /firstUseButton\.addEventListener\("click", openFirstUseGuide\)/);
  assert.match(source, /invoke\("first_run_start_core"\)/);
  assert.match(source, /settings\/index\.html\?guide=first-run/);
  assert.doesNotMatch(source, /first_run_guide_complete/);
  const startCore = rustMain.slice(
    rustMain.indexOf("async fn first_run_start_core("),
    rustMain.indexOf("struct RuntimeLogShutdown", rustMain.indexOf("async fn first_run_start_core(")),
  );
  assert.match(startCore, /spawn_blocking/);
  assert.match(startCore, /start_core_and_wait_available/);
});

test("completed migration exposes one explicit terminal action and no back action", () => {
  assert.match(source, /migrationContinueButton\.hidden = state !== "completed"/);
  assert.match(source, /migrationBackButton\.hidden = state === "completed"/);
  assert.match(source, /migrationContinueButton\.textContent = migrationRequiresSetup \? "继续首次设置" : "完成"/);
  assert.match(source, /await invoke\("resolve_settings_close", \{ discard: true \}\)/);
  const validationStart = legacyImportRust.indexOf("fn validate_with_core(");
  const validationEnd = legacyImportRust.indexOf("fn rollback_after_core_failure(", validationStart);
  assert.notEqual(validationStart, -1);
  assert.notEqual(validationEnd, -1);
  assert.doesNotMatch(
    legacyImportRust.slice(validationStart, validationEnd),
    /SETTINGS_WINDOW_LABEL[\s\S]*?\.hide\(\)/,
  );
});

test("welcome and route motion is bounded, directional, and focus-safe", () => {
  assert.match(styles, /--motion-fast:\s*120ms/);
  assert.match(styles, /--motion-state:\s*180ms/);
  assert.match(styles, /--motion-enter:\s*260ms/);
  assert.match(styles, /route-card-in/);
  assert.match(styles, /view-leave-forward/);
  assert.match(styles, /view-enter-forward/);
  assert.match(styles, /view-leave-backward/);
  assert.match(styles, /view-enter-backward/);
  assert.match(source, /let viewTransitionRunning = false/);
  assert.match(source, /if \(viewTransitionRunning \|\| !toView\.hidden\) return/);
  assert.match(source, /fromView\.inert = true/);
  assert.match(source, /focusTarget\.focus\(\{ preventScroll: true \}\)/);
  assert.match(source, /transitionViews\(routeView, migrationView, "forward", migrationBackButton\)/);
  assert.match(source, /transitionViews\(migrationView, routeView, "backward", migrationButton\)/);
});

test("migration motion follows real progress states without terminal loops", () => {
  for (const state of ["inspecting", "staging", "validating", "committing", "core_validating"]) {
    assert.match(source, new RegExp(`"${state}"`));
  }
  assert.match(source, /migrationView\.dataset\.migrationState = state/);
  assert.match(source, /previousProgressSnapshot/);
  assert.match(source, /const animationReplayHandles = new WeakMap\(\)/);
  assert.match(source, /element\.classList\.remove\(className\)[\s\S]*?animationReplayHandles\.delete\(element\)/);
  assert.match(source, /style\.setProperty\("--domain-order", boundedOrder\)/);
  assert.match(styles, /-webkit-appearance:\s*none/);
  assert.match(styles, /progress::-webkit-progress-value\s*\{[\s\S]*?transition:\s*width var\(--motion-progress\)/);
  assert.match(styles, /\.migration-progress\.is-completing\s*\{[\s\S]*?completion-settle var\(--motion-complete\)/);
  assert.match(styles, /#migrationContinueButton\.is-revealing\s*\{[\s\S]*?animation-delay:\s*110ms/);
  assert.doesNotMatch(styles, /animation\s*:[^;]*\binfinite\b/);
});

test("onboarding motion respects reduced-motion without delayed view swaps", () => {
  assert.match(source, /if \(reduceMotionQuery\?\.matches\) return Promise\.resolve\(\)/);
  assert.match(styles, /prefers-reduced-motion:\s*reduce/);
  assert.match(styles, /animation:\s*none !important/);
  assert.match(styles, /transition-duration:\s*0\.01ms !important/);
});

test("first-run startup queues onboarding without waiting for the paused pet WebView", () => {
  const setupStart = rustMain.indexOf(".setup(move |app|");
  const setupEnd = rustMain.indexOf(".on_menu_event", setupStart);
  assert.notEqual(setupStart, -1);
  assert.notEqual(setupEnd, -1);
  const setup = rustMain.slice(setupStart, setupEnd);
  assert.match(setup, /if !first_run_completed[\s\S]*dispatch_webview_product_menu_action\([\s\S]*ProductMenuAction::OpenSettings/);
  assert.doesNotMatch(setup, /product_shell::show_or_focus_settings\(/);

  const start = rustMain.indexOf("fn reveal_pet_window(");
  const end = rustMain.indexOf("fn commit_dragged_window_position(", start);
  assert.notEqual(start, -1);
  assert.notEqual(end, -1);
  const revealPetWindow = rustMain.slice(start, end);
  const firstRunBranch = revealPetWindow.slice(
    revealPetWindow.indexOf("if !first_run_completed"),
    revealPetWindow.indexOf("if !session_ready"),
  );
  assert.match(firstRunBranch, /window\.hide\(\)[\s\S]*return Ok\(\(\)\)/);
  assert.doesNotMatch(firstRunBranch, /dispatch_webview_product_menu_action|OpenSettings/);
  assert.match(revealPetWindow, /if !session_ready[\s\S]*dispatch_webview_product_menu_action\([\s\S]*ProductMenuAction::OpenSettings/);
  assert.doesNotMatch(revealPetWindow, /product_shell::show_or_focus_settings\(/);
});
