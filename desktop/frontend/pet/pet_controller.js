import { applyTheme } from "../core/theme.js";
import { BubbleAutoHideController } from "./bubble_controller.js";
import {
  applyLayoutVariables,
  computePetLayout,
  MAX_BUBBLE_HEIGHT,
  MIN_BUBBLE_HEIGHT,
} from "./layout.js";

function cloneLayout(layout) {
  return { ...(layout || {}) };
}

function normalizeRuntimeLayout(layout, { allowLegacy = false } = {}) {
  const normalized = cloneLayout(layout);
  if (
    allowLegacy &&
    normalized.control_panel_vertical_offset == null &&
    normalized.vertical_offset != null
  ) {
    normalized.control_panel_vertical_offset = normalized.vertical_offset;
  }
  delete normalized.vertical_offset;
  return normalized;
}

function boundedBubbleHeight(value) {
  const parsed = Number.parseInt(String(value ?? ""), 10);
  if (!Number.isFinite(parsed)) return 128;
  return Math.max(MIN_BUBBLE_HEIGHT, Math.min(MAX_BUBBLE_HEIGHT, parsed));
}

export class PetController {
  constructor({ store, invoke, portraitController, subtitleController, elements, emit = null }) {
    this.store = store;
    this.invoke = invoke;
    this.portraitController = portraitController;
    this.subtitleController = subtitleController;
    this.elements = elements;
    this.emit = emit || ((name, detail) => window.dispatchEvent(new CustomEvent(name, { detail })));
    this.composing = false;
    this.portraitNaturalSize = { width: 560, height: 570 };
    this.persistedLayout = {};
    this.previewSession = null;
    this.adaptiveBubbleHeight = null;
    this.subtitleActive = false;
    this.lastBusy = false;
    this.lastAudioSpeaking = false;
    this.layoutRequestedRevision = 0;
    this.layoutAppliedRevision = 0;
    this.pendingWindowLayout = null;
    this.appliedWindowLayout = null;
    this.layoutRunner = null;
    this.bubbleMeasurePending = false;
    this.bubbleAutoHide = new BubbleAutoHideController({
      target: elements.bubble,
      onHidden: () => this.resetAdaptiveBubbleHeight(),
    });
    this.unsubscribe = store.subscribe((state) => this.render(state));
    this.#bindInput();
  }

  applyBootstrap(bootstrap) {
    const persistedLayout = normalizeRuntimeLayout(bootstrap?.layout, { allowLegacy: true });
    this.persistedLayout = cloneLayout(persistedLayout);
    this.adaptiveBubbleHeight = null;
    this.store.setBootstrap({ ...bootstrap, layout: persistedLayout });
    if (this.previewSession) {
      this.previewSession.baselineLayout = cloneLayout(persistedLayout);
      this.store.setLayout(this.previewSession.currentLayout);
    }
    applyTheme(bootstrap.theme);
    this.bubbleAutoHide.configure(bootstrap.bubble);
    this.subtitleController.configure(bootstrap.subtitle);
    this.portraitController.setCharacter(bootstrap.character);
    this.subtitleController.setText(
      bootstrap.character?.initialMessage || "还没有角色，请打开角色工作室创建或导入角色。",
    );
    return this.applyLayout();
  }

  setPortraitNaturalSize(size) {
    if (size?.width > 0 && size?.height > 0) {
      this.portraitNaturalSize = size;
      return this.applyLayout();
    }
    return Promise.resolve();
  }

  setBusy(busy, interactionId = null) {
    this.store.setInteractionState({ busy: Boolean(busy), interactionId });
  }

  showSegments(segments) {
    this.subtitleController.showSegments(segments);
  }

  beginLayoutPreview(payload) {
    const sessionId = String(payload?.sessionId || "").trim();
    if (!sessionId) return Promise.resolve(false);
    const baselineLayout = normalizeRuntimeLayout(payload?.layout);
    this.persistedLayout = cloneLayout(baselineLayout);
    this.previewSession = {
      sessionId,
      revision: Math.max(0, Number(payload?.revision) || 0),
      baselineLayout: cloneLayout(baselineLayout),
      currentLayout: cloneLayout(baselineLayout),
    };
    this.adaptiveBubbleHeight = null;
    this.store.setLayout(baselineLayout);
    return this.applyLayout().then(() => true);
  }

  previewLayout(payload) {
    const sessionId = String(payload?.sessionId || "").trim();
    const revision = Math.max(0, Number(payload?.revision) || 0);
    if (!this.previewSession || this.previewSession.sessionId !== sessionId) {
      return Promise.resolve(false);
    }
    if (revision <= this.previewSession.revision) return Promise.resolve(false);
    const previous = this.previewSession.currentLayout;
    const next = normalizeRuntimeLayout(payload?.layout);
    this.previewSession.revision = revision;
    this.previewSession.currentLayout = cloneLayout(next);
    if (next.bubble_height !== previous.bubble_height) this.adaptiveBubbleHeight = null;
    this.store.setLayout(next);
    return this.applyLayout().then(() => true);
  }

  commitLayoutPreview(payload) {
    const sessionId = String(payload?.sessionId || "").trim();
    if (!this.previewSession || this.previewSession.sessionId !== sessionId) {
      return Promise.resolve(false);
    }
    const revision = Math.max(this.previewSession.revision, Number(payload?.revision) || 0);
    const layout = normalizeRuntimeLayout(payload?.layout);
    this.persistedLayout = cloneLayout(layout);
    this.previewSession = {
      sessionId,
      revision,
      baselineLayout: cloneLayout(layout),
      currentLayout: cloneLayout(layout),
    };
    this.adaptiveBubbleHeight = null;
    this.store.setLayout(layout);
    return this.applyLayout().then(() => true);
  }

  restoreLayoutPreview(payload) {
    const sessionId = String(payload?.sessionId || "").trim();
    if (!this.previewSession || this.previewSession.sessionId !== sessionId) {
      return Promise.resolve(false);
    }
    const baseline = cloneLayout(this.previewSession.baselineLayout || this.persistedLayout);
    this.previewSession = null;
    this.persistedLayout = cloneLayout(baseline);
    this.adaptiveBubbleHeight = null;
    this.store.setLayout(baseline);
    return this.applyLayout().then(() => true);
  }

  applyLayout() {
    const state = this.store.getState();
    const settings = state.layout || {};
    const scale = Math.max(0.5, Math.min(1.5, Number(settings.portrait_scale_percent || 100) / 100));
    const maxWidth = 560 * scale;
    const maxHeight = 570 * scale;
    const ratio = Math.min(
      maxWidth / this.portraitNaturalSize.width,
      maxHeight / this.portraitNaturalSize.height,
    );
    const layout = computePetLayout({
      portraitWidth: Math.round(this.portraitNaturalSize.width * ratio),
      portraitHeight: Math.round(this.portraitNaturalSize.height * ratio),
      controlPanelWidth: settings.control_panel_width,
      bubbleHeight: this.adaptiveBubbleHeight ?? settings.bubble_height,
      controlPanelVerticalOffset: settings.control_panel_vertical_offset,
      inputBarOffset: settings.input_bar_offset,
    });
    applyLayoutVariables(layout);
    document.documentElement.style.setProperty(
      "--speech-font-size",
      `${settings.speech_font_size || 16}px`,
    );
    document.documentElement.style.setProperty(
      "--name-font-size",
      `${settings.name_font_size || 13}px`,
    );
    document.documentElement.style.setProperty(
      "--input-font-size",
      `${settings.input_font_size || 15}px`,
    );
    document.documentElement.style.setProperty(
      "--button-font-size",
      `${settings.button_font_size || 13}px`,
    );

    this.layoutRequestedRevision += 1;
    this.pendingWindowLayout = { revision: this.layoutRequestedRevision, layout };
    if (!this.layoutRunner) {
      this.layoutRunner = this.#drainWindowLayouts().finally(() => {
        this.layoutRunner = null;
      });
    }
    return this.layoutRunner;
  }

  handleSubtitleSpeaking() {
    this.subtitleActive = true;
    this.resetAdaptiveBubbleHeight();
    this.bubbleAutoHide.notifySpeaking();
  }

  handleSubtitleTextChanged() {
    this.#scheduleBubbleMeasurement();
  }

  #scheduleBubbleMeasurement() {
    if (this.bubbleMeasurePending) return;
    this.bubbleMeasurePending = true;
    const schedule = window.requestAnimationFrame || ((callback) => queueMicrotask(callback));
    schedule(() => {
      this.bubbleMeasurePending = false;
      this.fitBubbleToContent();
    });
  }

  handleSubtitleSettled() {
    this.subtitleActive = false;
    this.fitBubbleToContent();
    const state = this.store.getState();
    if (!state.interaction.busy && !state.audio.speaking) this.bubbleAutoHide.notifySettled();
  }

  fitBubbleToContent(measuredHeight = this.elements.bubble?.scrollHeight) {
    const needed = Math.ceil(Number(measuredHeight) || 0);
    const base = boundedBubbleHeight(this.store.getState().layout?.bubble_height);
    const current = this.adaptiveBubbleHeight ?? base;
    if (needed <= current || current >= MAX_BUBBLE_HEIGHT) return false;
    const fontSize = Number(this.store.getState().layout?.speech_font_size) || 16;
    const lineHeight = Math.max(1, Math.ceil(fontSize * 1.65));
    const next = Math.min(MAX_BUBBLE_HEIGHT, current + lineHeight);
    if (next === current) return false;
    this.adaptiveBubbleHeight = next;
    this.applyLayout().catch(() => {});
    if (needed > next && next < MAX_BUBBLE_HEIGHT) this.#scheduleBubbleMeasurement();
    return true;
  }

  resetAdaptiveBubbleHeight() {
    if (this.adaptiveBubbleHeight == null) return false;
    this.adaptiveBubbleHeight = null;
    this.applyLayout().catch(() => {});
    return true;
  }

  handleUserInteraction() {
    this.bubbleAutoHide.handleUserInteraction();
    this.handleSubtitleTextChanged();
  }

  render(state) {
    const busy = Boolean(state.interaction.busy);
    const speaking = Boolean(state.audio?.speaking);
    const hasCharacter = Boolean(state.character);
    if ((busy && !this.lastBusy) || (speaking && !this.lastAudioSpeaking)) {
      this.bubbleAutoHide.notifySpeaking();
    } else if (
      !busy &&
      !speaking &&
      !this.subtitleActive &&
      (this.lastBusy || this.lastAudioSpeaking)
    ) {
      this.bubbleAutoHide.notifySettled();
    }
    this.lastBusy = busy;
    this.lastAudioSpeaking = speaking;
    document.documentElement.dataset.speaking = String(speaking);
    document.documentElement.dataset.hasCharacter = String(hasCharacter);
    this.elements.characterName.textContent = state.character?.displayName || "Sakura";
    this.elements.input.disabled = busy || !hasCharacter;
    this.elements.input.placeholder = hasCharacter ? "输入消息…" : "请先创建或导入角色";
    this.elements.send.hidden = busy;
    this.elements.send.disabled = !hasCharacter;
    this.elements.cancel.hidden = !busy;
    this.elements.capture.disabled = busy || !hasCharacter;
    this.elements.capture.textContent = state.observation?.attached ? "已截" : "截";
    if (this.elements.openSettingsButton) {
      this.elements.openSettingsButton.disabled = !hasCharacter;
    }
    if (this.elements.openHistoryButton) {
      this.elements.openHistoryButton.disabled = !hasCharacter;
    }
  }

  async #drainWindowLayouts() {
    while (this.layoutAppliedRevision < this.layoutRequestedRevision) {
      const pending = this.pendingWindowLayout;
      const previous = this.appliedWindowLayout;
      const [width, height] = pending.layout.windowSize;
      const [portraitAnchorX, portraitAnchorY] = pending.layout.portraitAnchor;
      await this.invoke("apply_pet_window_layout", {
        width,
        height,
        bottomMargin: 24,
        portraitAnchorX,
        portraitAnchorY,
        previousPortraitAnchorX: previous?.portraitAnchor?.[0] ?? null,
        previousPortraitAnchorY: previous?.portraitAnchor?.[1] ?? null,
      });
      this.appliedWindowLayout = pending.layout;
      this.layoutAppliedRevision = pending.revision;
    }
  }

  #bindInput() {
    const { input, send, cancel, capture } = this.elements;
    input.addEventListener("compositionstart", () => {
      this.composing = true;
    });
    input.addEventListener("compositionend", () => {
      this.composing = false;
    });
    input.addEventListener("focus", () => this.handleUserInteraction());
    input.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" || this.composing || event.isComposing) return;
      event.preventDefault();
      this.#sendMessage();
    });
    send.addEventListener("click", () => this.#sendMessage());
    cancel.addEventListener("click", () => {
      this.emit("sakura:chat-cancel", {
        interactionId: this.store.getState().interaction.interactionId,
      });
    });
    capture.addEventListener("click", () => this.emit("sakura:capture-request", {}));
  }

  #sendMessage() {
    const text = this.elements.input.value.trim();
    const state = this.store.getState();
    if ((!text && !state.observation?.attached) || state.interaction.busy) return;
    this.emit("sakura:chat-send", {
      text,
      observationId: state.observation?.observationId || null,
    });
  }
}
