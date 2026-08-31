import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const html = await readFile(new URL("../settings/index.html", import.meta.url), "utf8");
const settingsJs = await readFile(new URL("../settings/settings.js", import.meta.url), "utf8");
const settingsCss = await readFile(new URL("../settings/styles.css", import.meta.url), "utf8");
const appJs = await readFile(new URL("../app.js", import.meta.url), "utf8");
const rustMain = await readFile(new URL("../../src-tauri/src/main.rs", import.meta.url), "utf8");
const productShell = await readFile(new URL("../../src-tauri/src/product_shell.rs", import.meta.url), "utf8");

test("one unreadable settings domain does not block later plugin initialization", () => {
  assert.match(settingsJs, /async function initializeRuntimeSettingsSection\(initialize\)/);
  const screenStart = settingsJs.indexOf('featureStatus(manifest, "privacy.screen_awareness")');
  const pluginStart = settingsJs.indexOf('featureStatus(manifest, "plugins.manage")');
  assert.notEqual(screenStart, -1);
  assert.notEqual(pluginStart, -1);
  assert.ok(screenStart < pluginStart);
  assert.match(
    settingsJs.slice(screenStart, pluginStart),
    /initializeRuntimeSettingsSection[\s\S]*settings_screen_awareness_get/,
  );
  assert.match(
    settingsJs.slice(pluginStart, settingsJs.indexOf('featureStatus(manifest, "voice.tts")')),
    /initializeRuntimeSettingsSection[\s\S]*settings_plugins_get/,
  );
});

test("settings toasts stay clear of footer actions and use a neutral frame", () => {
  const toastStack = settingsCss.match(/\.toast-stack\s*\{[\s\S]*?\}/)?.[0] || "";
  const toast = settingsCss.match(/\.toast\s*\{[\s\S]*?\}/)?.[0] || "";

  assert.match(toastStack, /top:\s*18px/);
  assert.doesNotMatch(toastStack, /bottom:/);
  assert.doesNotMatch(toast, /border-left/);
  assert.doesNotMatch(settingsCss, /\.toast\.is-(?:success|info|error)\s*\{/);
});

test("settings surfaces do not use decorative left-edge accent rails", () => {
  assert.doesNotMatch(settingsCss, /border-(?:left|inline-start)\s*:/);
  assert.doesNotMatch(settingsCss, /\.nav-item::before/);
});

test("system owns updates, help, and storage while legacy toggles are absent", () => {
  const systemPage = html.match(/<section id="page-system"[\s\S]*?<\/section>/)?.[0] || "";
  assert.match(systemPage, /<legend>应用更新<\/legend>/);
  assert.match(systemPage, /id="updateAutoCheck"/);
  assert.match(systemPage, /<legend>使用帮助<\/legend>/);
  assert.match(systemPage, /id="systemFirstRunGuideButton"/);
  assert.match(systemPage, /id="storageUserRoot"/);
  assert.match(systemPage, /id="legacyRoleDataImportButton"[\s\S]*?选择 0\.9\.x 目录/);
  assert.match(settingsJs, /legacyRoleDataImportChoose/);
  assert.match(settingsJs, /确认覆盖冲突记录/);
  assert.match(
    settingsJs,
    /LEGACY_IMPORT_CORE_STOP_FAILED[\s\S]*无法确认旧版本迁移进程和 Sakura Core 已停止/,
  );
  assert.match(systemPage, /id="storageTtsRoot"/);
  assert.match(settingsJs, /system: \{ title: "系统", subtitle: "管理应用更新、使用帮助与本地数据" \}/);
  assert.doesNotMatch(html, /data-page="storage"|id="page-storage"|数据与存储/);
  assert.doesNotMatch(html, /agentTraceEnabled|debugLogEnabled|launchAtLogin|调试日志/);
});

test("about page exposes compact product links, sponsorship, and update checks", () => {
  const aboutPage = html.match(/<section id="page-about"[\s\S]*?<\/section>/)?.[0] || "";
  assert.match(aboutPage, /id="aboutWebsiteButton"/);
  assert.match(aboutPage, /id="aboutRepositoryButton"/);
  assert.match(aboutPage, /id="aboutChangelogButton"/);
  assert.match(aboutPage, /id="aboutSponsorButton"/);
  assert.doesNotMatch(aboutPage, /FirstRunGuideButton|新手引导/);
  assert.match(aboutPage, /id="aboutVersion"/);
  assert.match(aboutPage, /id="updateCheckButton"/);
  assert.doesNotMatch(aboutPage, /id="updateAutoCheck"/);
  assert.match(aboutPage, /id="updateActionButton"/);
  assert.match(aboutPage, /id="aboutComponentsSummary"/);
  assert.match(aboutPage, /id="aboutComponentsRefresh"/);
  assert.match(aboutPage, /id="aboutComponentsList"/);
  assert.doesNotMatch(aboutPage, /始终陪在桌面的 AI 角色助手|aboutRepositoryUrl|<fieldset/);
});

test("interaction owns screen awareness and omits unimplemented backchannel settings", () => {
  const interactionPage = html.match(/<section id="page-interaction"[\s\S]*?<\/section>/)?.[0] || "";
  assert.match(interactionPage, /主动屏幕感知/);
  assert.match(interactionPage, /id="screenResolution"/);
  assert.doesNotMatch(html, /data-page="privacy"|id="page-privacy"/);
  assert.doesNotMatch(html, /backchannel|快速接话|<legend>接话<\/legend>/i);
});

test("model context budget is an advanced parameter with one-million-token support", () => {
  const modelPage = html.match(/<section id="page-model"[\s\S]*?<\/section>/)?.[0] || "";
  const modelSlots = modelPage.match(/<fieldset class="settings-group">[\s\S]*?<\/fieldset>/)?.[0] || "";

  assert.match(modelPage, /<summary>高级参数<\/summary>[\s\S]*?id="contextWindowTokens"/);
  assert.match(modelPage, /上下文预算 \(tokens\)[\s\S]*?留空默认 32K/);
  assert.match(modelPage, /min="4096" max="2000000"[\s\S]*?例如 1000000/);
  assert.doesNotMatch(modelPage, /上下文窗口/);
  assert.doesNotMatch(modelSlots, /上下文预算|contextWindowTokens/);
  assert.doesNotMatch(modelPage, /memoryModelResourceCard|本地记忆模型|resource-foldout/);
});

test("voice and model pages do not duplicate component download controls", () => {
  const voicePage = html.match(/<section id="page-voice"[\s\S]*?<\/section>/)?.[0] || "";
  assert.doesNotMatch(voicePage, /ttsResourceCard|整合包|重新安装|在线安装/);
  assert.doesNotMatch(html, /id="memoryModelResourceCard"/);
  assert.doesNotMatch(settingsJs, /renderMemoryModelResourceCard/);
});

test("voice availability is resolved through the TTS Service rather than an official plugin ID", () => {
  assert.doesNotMatch(settingsJs, /pluginId\s*===\s*["']sakura\.tts["']/);
});

test("about component actions restore focus by resource when the action label changes", () => {
  assert.match(settingsJs, /options\.focusActions \? resourceKey : ""/);
  assert.match(settingsJs, /renderAboutComponents\(\{ restoreResourceKey: restoreAboutResourceKey \}\)/);
});

test("runtime character switching tolerates missing legacy Memory settings", () => {
  const renderer = settingsJs.match(
    /function renderMemoryStatus\(\)[\s\S]*?function renderMemoryList\(\)/,
  )?.[0] || "";
  assert.doesNotMatch(renderer, /request\.memory\.curation\.trigger_turns/);
  assert.match(renderer, /request\?\.memory\?\.curation\?\.trigger_turns/);
});

test("unmigrated character actions do not grey their migrated shared rows", () => {
  assert.match(
    settingsJs,
    /fields\.characterEditorButton,[\s\S]*?fields\.characterExportButton,[\s\S]*?disableRuntimeControl\(control, \{ markRow: false \}\)/,
  );
});

test("runtime character selection is staged until the aggregate save flow commits it", () => {
  const stagedSelection = settingsJs.match(
    /function stageRuntimeCharacterSelection\(\)[\s\S]*?async function importCharacterVoiceArchive\(\)/,
  )?.[0] || "";
  const runtimeSave = settingsJs.match(
    /async function saveRuntimeSettings\(\)[\s\S]*?function collectTtsSettings\(\)/,
  )?.[0] || "";

  assert.match(stagedSelection, /runtimeCharacterDraftId = characterId/);
  assert.doesNotMatch(stagedSelection, /characterSelect\(/);
  assert.match(runtimeSave, /commitCharacterSelection\(/);
  assert.match(
    settingsJs,
    /if \(runtimeSettingsHost\) void stageRuntimeCharacterSelection\(\)/,
  );
  assert.match(stagedSelection, /previewRuntimeCharacterVisual\(characterId\)/);
  assert.match(settingsJs, /settings_character_visual_preview/);
});

test("discard restores the committed runtime character selection", () => {
  const discardSelection = settingsJs.match(
    /function discardRuntimeCharacterSelection\(\)[\s\S]*?function clearCharacterScopedRuntimeState\(\)/,
  )?.[0] || "";

  assert.match(
    discardSelection,
    /runtimeCharacterDraftId = runtimeCharacterSnapshot\?\.currentCharacterId \|\| ""/,
  );
  assert.match(discardSelection, /fields\.characterSelect\.value = runtimeCharacterDraftId/);
  assert.match(settingsJs, /discard:[\s\S]*?discardRuntimeCharacterSelection\(\)/);
});

test("pending character selection locks old character surfaces and recognizes plugin Memory drafts", () => {
  const archiveState = settingsJs.match(
    /function syncCharacterArchiveState\(\)[\s\S]*?function setCharacterArchiveBusy\(/,
  )?.[0] || "";
  const draftGate = settingsJs.match(
    /function currentCharacterHasDrafts\(\)[\s\S]*?function pendingRuntimeCharacterId\(/,
  )?.[0] || "";

  assert.match(archiveState, /fields\.pages\.appearance, fields\.pages\.voice, fields\.pages\.memory/);
  assert.match(archiveState, /characterSwitching \|\| Boolean\(pendingCharacterId\)/);
  assert.match(draftGate, /memorySettingsDirty: runtimeMemoryController\?\.isDirty\(\)/);
  assert.match(draftGate, /countCharacterScopedCollectionDrafts\(pluginCollectionState\.values\(\)\)/);
});

test("character draft preview changes theme, portrait, and greeting without rebinding chat identity", () => {
  const visualPreview = appJs.match(
    /listenAppEvent\("sakura:\/\/character-visual-preview"[\s\S]*?listenAppEvent\("sakura:\/\/control-surface-frame"/,
  )?.[0] || "";

  assert.match(visualPreview, /validateCharacterPresentation\(publication\.presentation\)/);
  assert.match(
    appJs,
    /import \{[\s\S]*?validateCharacterPresentation,[\s\S]*?\} from "\.\/pet\/character-presentation\.js";/,
  );
  assert.match(visualPreview, /portraitCurrent\.src = source/);
  assert.match(visualPreview, /portraitResourceId: previewPresentation\.portraitResourceIds\[key\]/);
  assert.match(visualPreview, /applyTheme\(previewAppearance\.themeTokens\)/);
  assert.match(
    visualPreview,
    /bubbleScroll\.updateText\(previewPresentation\.initialMessage, \{ forceEnd: true \}\)/,
  );
  assert.doesNotMatch(visualPreview, /characterPresentation\s*=\s*previewPresentation/);
  assert.doesNotMatch(visualPreview, /presentation\s*=\s*rebindCharacterPresentation/);
  assert.doesNotMatch(visualPreview, /characterName\.textContent|input\.placeholder/);
  assert.match(visualPreview, /characterVisualPreviewSessions\.isCurrent\(previewToken\)/);
  assert.match(settingsJs, /await runtimeCharacterVisualPreviewPromise/);
});

test("settings window defaults to a roomier 1080p-friendly size", () => {
  assert.match(productShell, /\.inner_size\(1200\.0, 800\.0\)/);
  assert.match(productShell, /\.min_inner_size\(900\.0, 640\.0\)/);
});

test("character archives use native open and save dialogs without browser prompts", () => {
  assert.match(settingsJs, /invoke\("settings_character_choose_import", \{ kind \}\)/);
  assert.match(settingsJs, /invoke\("settings_character_choose_export", \{/);
  assert.doesNotMatch(settingsJs, /window\.__TAURI__\?\.dialog/);
  assert.doesNotMatch(settingsJs, /请输入文件完整路径|请输入保存路径/);
  assert.match(rustMain, /async fn settings_character_choose_import\(/);
  assert.match(rustMain, /async fn settings_character_choose_export\(/);
  assert.match(rustMain, /add_filter\(filter_name, &\[extension\]\)/);
  assert.match(rustMain, /settings_character_choose_import,[\s\S]*settings_character_choose_export,/);
});

test("update installation keeps an independent busy lock across manual rechecks", () => {
  assert.match(settingsJs, /let updateActionBusy = false/);
  assert.match(settingsJs, /if \(updateActionBusy\) return;[\s\S]*?async function saveUpdatePreferences/);
  assert.match(settingsJs, /updateActionBusy = true;[\s\S]*?fields\.updateCheckButton\.disabled = true/);
  assert.match(settingsJs, /fields\.updateActionButton\.disabled = updateActionBusy/);
});

test("proactive update idle wiring rejects whitespace drafts and active IME composition", async () => {
  const appJs = await readFile(new URL("../app.js", import.meta.url), "utf8");
  const updateWiring = appJs.match(/const updateAnnouncement = createUpdateAnnouncementController\([\s\S]*?\n\}\);/)?.[0] || "";
  assert.match(updateWiring, /input\.value === ""/);
  assert.match(updateWiring, /stage\.dataset\.composing !== "true"/);
  assert.doesNotMatch(updateWiring, /input\.value\.trim\(\)/);
});

test("available updates become a top-level versioned action with a themed status strip", () => {
  const aboutPage = html.match(/<section id="page-about"[\s\S]*?<section class="about-components"/)?.[0] || "";
  const actions = aboutPage.match(/<div class="about-product-actions">[\s\S]*?<\/div>/)?.[0] || "";

  assert.match(actions, /id="updateCheckButton"[\s\S]*id="updateActionButton"/);
  assert.match(aboutPage, /id="updateFeedback"[^>]*role="status"[^>]*aria-live="polite"/);
  assert.match(settingsJs, /updateCachedGet\(\)/);
  assert.match(settingsJs, /`更新到 v\$\{snapshot\.version\}`/);
  assert.match(settingsJs, /fields\.updateFeedback\.hidden = !snapshot\.available/);
  assert.match(settingsJs, /dataset\.state = snapshot\.available \? "available" : "current"/);
  assert.match(settingsCss, /\.about-update-feedback\[data-state="available"\]/);
});
