import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const settings = readFileSync(new URL("../settings/settings.js", import.meta.url), "utf8");
const styles = readFileSync(new URL("../settings/styles.css", import.meta.url), "utf8");

function functionSource(name, nextName) {
  const start = settings.indexOf(`async function ${name}`);
  const end = settings.indexOf(`\nfunction ${nextName}`, start);
  assert.ok(start >= 0 && end > start, `${name} source must be discoverable`);
  return settings.slice(start, end);
}

function sourceBetween(startNeedle, endNeedle) {
  const start = settings.indexOf(startNeedle);
  const end = settings.indexOf(endNeedle, start);
  assert.ok(start >= 0 && end > start, `${startNeedle} source must be discoverable`);
  return settings.slice(start, end);
}

test("Memory CRUD keeps the loaded archive mounted through reconciliation", () => {
  const mutation = functionSource("mutatePluginCollection", "renderPluginCollection");
  const memoryStart = mutation.indexOf("applyMemoryCollectionMutationResult");
  const memoryEnd = mutation.indexOf("    } else {", memoryStart);
  assert.ok(memoryStart >= 0 && memoryEnd > memoryStart);
  const memorySuccess = mutation.slice(memoryStart, memoryEnd);

  assert.match(memorySuccess, /queryPluginCollection\(plugin, section, collection, \{ render: false \}\)/);
  assert.doesNotMatch(memorySuccess, /state\.loaded\s*=\s*false/);
  assert.equal(
    [...memorySuccess.matchAll(/renderMemorySurface\(\)/g)].length,
    1,
    "a successful Memory mutation commits one surface render",
  );
  assert.ok(memorySuccess.indexOf("dismissMemoryEditorPortal") < memorySuccess.indexOf("renderMemorySurface"));
  assert.match(memorySuccess, /operation === "delete"\) await animateMemoryRecordRemoval/);
  assert.match(memorySuccess, /state\.motion = \{ kind: operation, itemId: affectedItemId \}/);

  const query = functionSource("queryPluginCollection", "pluginCollectionFieldControl");
  assert.match(query, /render && !state\.loaded/);
  assert.match(query, /render && section\.surface === "memory"/);
});

test("Memory CRUD motion is localized, reversible, and reduced-motion safe", () => {
  assert.match(settings, /syncMemoryEditorPortalState\(state\)/);
  assert.match(settings, /removeOverlayAfterExit\(overlay\)/);
  assert.match(settings, /card\.dataset\.itemId = item\.itemId/);
  assert.match(styles, /\.memory-record-card\.is-entering\s*\{/);
  assert.match(styles, /\.memory-record-card\.is-removing\s*\{/);
  assert.match(styles, /\.memory-editor-overlay\.is-closing\s*\{/);
  assert.match(styles, /\.confirm-overlay\.is-closing\s*\{/);
  assert.match(styles, /@keyframes memory-record-enter/);
  assert.match(styles, /@keyframes memory-record-remove/);
  assert.match(styles, /@media \(prefers-reduced-motion: reduce\)[\s\S]*?animation-duration: 0\.001ms !important/);
});

test("Memory working state blocks Collection reads and disables archive controls", () => {
  const query = functionSource("queryPluginCollection", "pluginCollectionFieldControl");
  const archive = sourceBetween("function renderMemoryCollection", "function renderMemorySurface");

  assert.match(query, /section\.surface === "memory" && memoryActivityBlocksCollection\(projectPluginActivity\(plugin\)\)/);
  assert.match(archive, /const initializing = activity\.state === "working"/);
  assert.match(archive, /const activityControlsDisabled = initializing \|\| activityUnavailable/);
  assert.match(archive, /add\.disabled = activityControlsDisabled \|\| state\.loading/);
  assert.match(archive, /search\.disabled = activityControlsDisabled/);
  assert.match(archive, /select\.disabled = activityControlsDisabled/);
  assert.match(archive, /count\.textContent = initializing[\s\S]*?"正在初始化"/);
  assert.match(archive, /refresh\.disabled = activityControlsDisabled \|\| state\.loading/);
  assert.match(archive, /if \(!activityControlsDisabled && !state\.loaded && !state\.loading && !state\.error\)/);
});

test("Memory warning and error states show safe plugin-owned guidance", () => {
  const notice = sourceBetween(
    "function createMemoryActivityNotice",
    "function renderMemoryPreparingArchive",
  );
  const archive = sourceBetween("function renderMemoryCollection", "function renderMemorySurface");

  assert.match(settings, /\["warning", "error", "disabled", "failed"\]\.includes\(activity\?\.state\)/);
  assert.match(notice, /activity\.message \|\| pluginFailure\?\.message/);
  assert.match(notice, /\["error", "failed"\]\.includes\(activity\.state\) \? "alert" : "status"/);
  assert.match(notice, /link\.textContent = "前往插件页"/);
  assert.match(notice, /pluginState\.selectedId = plugin\.id[\s\S]*?showPage\("plugins"\)/);
  assert.match(archive, /else if \(activityUnavailable\)[\s\S]*?createMemoryActivityNotice\(plugin, activity\)/);
  assert.match(archive, /!activityUnavailable && state\.loading && !state\.loaded/);
  assert.match(archive, /!activityUnavailable && state\.loaded && !state\.items\.length/);
  assert.match(archive, /else if \(!initializing && !activityUnavailable\) \{/);
});

test("visible transient plugin activity uses one bounded refresh loop", () => {
  const showPage = sourceBetween("function showPage", "function isOnboarding");
  const polling = sourceBetween(
    "function memorySurfaceIsTransitioning",
    "function memoryCollectionOptionLabel",
  );

  assert.match(polling, /projectPluginActivity\(plugin\)\.state === "working"/);
  assert.match(polling, /projectPluginActivity\(plugin\)\.isTransient/);
  assert.match(polling, /window\.setTimeout\(refreshPluginActivityCurrent, 1200\)/);
  assert.match(polling, /if \(pluginActivityRefreshInFlight/);
  assert.match(polling, /catch \{[\s\S]*?finally \{[\s\S]*?schedulePluginActivityRefresh\(\)/);
  assert.doesNotMatch(polling, /setInterval/);
  assert.match(showPage, /clearPluginActivityRefresh\(\)/);
  assert.match(settings, /beforeunload[\s\S]*?clearPluginActivityRefresh\(\)/);
});

test("Memory initialization renders the shared accessible thread animation", () => {
  const preparing = sourceBetween(
    "function createMemoryPreparingState",
    "function renderMemoryPreparingArchive",
  );
  const archive = sourceBetween(
    "function renderMemoryPreparingArchive",
    "function renderMemoryCollection",
  );
  const collection = sourceBetween("function renderMemoryCollection", "function renderMemorySurface");
  const surface = sourceBetween("function renderMemorySurface", "async function runPluginSettingsAction");
  const threadStyles = styles.slice(
    styles.indexOf(".memory-archive-list.is-preparing"),
    styles.indexOf(".memory-surface-error"),
  );

  assert.equal([...preparing.matchAll(/class="memory-thread-branch/g)].length, 3);
  assert.match(preparing, /setAttribute\("role", "status"\)/);
  assert.match(preparing, /setAttribute\("aria-live", "polite"\)/);
  assert.match(preparing, /setAttribute\("aria-busy", "true"\)/);
  assert.match(preparing, /正在准备长期记忆/);
  assert.doesNotMatch(preparing, /本地档案准备完成后会自动显示/);
  assert.match(archive, /memory-archive-head/);
  assert.match(archive, /memory-archive-toolbar/);
  assert.match(archive, /headActions\.append\(count, add\)/);
  assert.match(collection, /headActions\.append\(count, add\)/);
  assert.doesNotMatch(archive, /长期记忆 · 本地档案|管理当前角色的长期记忆/);
  assert.doesNotMatch(collection, /memory-eyebrow|collection\.description/);
  assert.match(archive, /createMemoryPreparingState\(\)/);
  assert.match(collection, /createMemoryPreparingState\(\)/);
  assert.match(surface, /memorySurfaceIsTransitioning\(\)[\s\S]*?renderMemoryPreparingArchive\(\)/);
  assert.match(threadStyles, /var\(--sakura-border\)/);
  assert.match(threadStyles, /var\(--sakura-primary\)/);
  assert.match(threadStyles, /animation: memory-thread-converge 2\.2s linear infinite/);
  assert.doesNotMatch(threadStyles, /#[0-9a-f]{3,8}|rgba?\(|gradient\(/i);
  assert.match(styles, /@media \(prefers-reduced-motion: reduce\)[\s\S]*?\.memory-thread-flow\s*\{[\s\S]*?animation: none;[\s\S]*?stroke-dasharray: none;/);
});
