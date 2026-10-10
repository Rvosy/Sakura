import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

import { applyTheme, FALLBACK_THEME_TOKENS } from "../core/theme.js";
import { validateAppearancePublication } from "../pet/appearance.js";
import { validateCharacterPresentation } from "../pet/character-presentation.js";

const source = await readFile(new URL("../app.js", import.meta.url), "utf8");
const startup = source.slice(source.indexOf("let characterPresentation;"), source.indexOf("let portraitHitRevision = 0;"));

for (const sessionBlockedAtStartup of [false, true]) {
  test(`unavailable startup presentation provides a complete native effect theme (session blocked: ${sessionBlockedAtStartup})`, async () => {
    const commands = [];
    const reports = [];
    const notices = [];
    const root = { style: { setProperty() {} }, dataset: {} };
    let paintedTheme;
    const context = {
      sessionBlockedAtStartup,
      presentationUnavailable: false,
      activeAppearance: null,
      inputVisualEffectFallbackActive: false,
      loadCurrentCharacterPresentation: async () => { throw new Error("CHARACTER_PRESENTATION_UNAVAILABLE"); },
      validateCharacterPresentation,
      validateAppearancePublication,
      runtimeDiagnostics: { reportError: (...args) => reports.push(args) },
      invoke: async () => { throw new Error("CHARACTER_PRESENTATION_UNAVAILABLE"); },
      showRecoverableError: (message) => notices.push(message),
      clearRecoverableError: () => assert.fail("the startup failure must remain visible"),
      createCharacterVisualPreviewSessionController: () => ({}),
      applyTheme: (tokens) => { paintedTheme = applyTheme(tokens, root); },
      applyAppearanceVariables() {},
      applyInputVisualEffect: async (values) => {
        // The native command validates the full appearance, including all 11 theme colors.
        validateAppearancePublication({
          schemaVersion: 1, coreGenerationId: "unavailable", characterId: "unavailable", values,
        }, { generationId: "unavailable", characterId: "unavailable" });
        commands.push(values);
      },
      characterName: {},
      input: {},
      portraitFallbackName: {},
      portrait: { setAttribute() {} },
      composerPlaceholder: () => "",
    };
    await vm.runInNewContext(`(async () => { ${startup} })()`, context);
    assert.equal(context.presentationUnavailable, true);
    assert.equal(commands.length, 1);
    assert.deepEqual(commands[0].themeTokens, FALLBACK_THEME_TOKENS);
    assert.deepEqual(commands[0].themeTokens, paintedTheme);
    assert.equal(reports.length, sessionBlockedAtStartup ? 0 : 1);
    assert.equal(notices.length, sessionBlockedAtStartup ? 0 : 1);
  });
}

for (const visualReasonCode of ["VISUAL_PROVIDER_MISSING", "VISUAL_RENDERER_FAILED"]) {
  test(`rebind keeps ready chat usable when visuals fail: ${visualReasonCode}`, async () => {
    const { createRendererHost } = await import("../pet/renderer-host.js");
    const { browserFixture } = await import("./fixtures/plugin-settings-fixture.js");
    const { document } = browserFixture();
    const failures = [];
    const container = document.createElement("div");
    container.ownerDocument = document;
    const rendererHost = createRendererHost({ container,
      loadModule: async () => { throw new Error("renderer import failed"); },
      onUnavailable: code => failures.push(code) });
    const state = { lifecycle: "ready", silentInteraction: false, canRetry: false };
    const next = { generationId: "g", characterId: "character", displayName: "Character",
      visual: visualReasonCode === "VISUAL_RENDERER_FAILED" ? { bindingId: "new", renderer: "fixture" } : null,
      visualReasonCode };
    const rebind = source.slice(source.indexOf("async function rebindCoreGeneration("), source.indexOf('\nawait listenAppEvent("sakura://character-visual-preview"'));
    const controls = source.slice(source.indexOf("  input.disabled = presentationUnavailable;"), source.indexOf("  replyHistoryPrevious.disabled"));
    const context = {
      coreRebindRevision: 0, coreRebindTarget: "", disposed: false, presentationUnavailable: true,
      characterPresentation: { ...next, visualReasonCode: "READY" },
      loadCurrentCharacterPresentation: async () => next, invoke() {}, rendererHost,
      characterVisualPreviewSessions: { invalidate() {} }, characterVisualPreviewActive: false, portraitHitRevision: 0,
      presentation: { current: () => state },
      rebindCharacterPresentation: ({ currentReducer }) => ({ reducer: currentReducer, characterChanged: false }),
      pendingCharacterGreeting: false, activeAppearance: { portraitScalePercent: 100 },
      validateAppearancePublication: () => ({ portraitScalePercent: 100 }), visualScalePercent: 100,
      characterName: {}, portraitFallbackName: {}, portrait: { setAttribute() {} },
      applyTheme() {}, applyAppearanceVariables() {}, adaptiveSurface: { invalidate() {} }, render() {},
      runtimeDiagnostics: { reportError: error => assert.fail(String(error)) }, showRecoverableError: assert.fail,
      input: {}, send: {}, asrController: null, state, isChatReadyLifecycle: lifecycle => lifecycle === "ready",
    };
    vm.createContext(context);
    assert.equal(await vm.runInContext(`${rebind}\nrebindCoreGeneration("g", { refresh: true })`, context), true);
    vm.runInContext(controls, context);
    assert.equal(context.input.disabled, false);
    assert.equal(context.send.disabled, false);
    assert.deepEqual(failures, [visualReasonCode]);
    rendererHost.destroy();
  });
}
