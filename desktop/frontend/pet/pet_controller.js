import { applyTheme } from "../core/theme.js";
import { applyLayoutVariables, computePetLayout } from "./layout.js";

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
    this.unsubscribe = store.subscribe((state) => this.render(state));
    this.#bindInput();
  }

  applyBootstrap(bootstrap) {
    this.store.setBootstrap(bootstrap);
    applyTheme(bootstrap.theme);
    this.subtitleController.configure(bootstrap.subtitle);
    this.portraitController.setCharacter(bootstrap.character);
    this.subtitleController.setText(
      bootstrap.character?.initialMessage || "还没有角色，请打开角色工作室创建或导入角色。",
    );
    this.applyLayout();
  }

  setPortraitNaturalSize(size) {
    if (size?.width > 0 && size?.height > 0) {
      this.portraitNaturalSize = size;
      this.applyLayout();
    }
  }

  setBusy(busy, interactionId = null) {
    this.store.setInteractionState({ busy: Boolean(busy), interactionId });
  }

  showSegments(segments) {
    this.subtitleController.showSegments(segments);
  }

  previewLayout(layout) {
    this.store.setLayout(layout);
    return this.applyLayout();
  }

  async applyLayout() {
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
      bubbleHeight: settings.bubble_height,
      verticalOffset: settings.vertical_offset,
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
    const [width, height] = layout.windowSize;
    await this.invoke("apply_pet_window_layout", { width, height, bottomMargin: 24 });
  }

  render(state) {
    const busy = Boolean(state.interaction.busy);
    const hasCharacter = Boolean(state.character);
    document.documentElement.dataset.speaking = String(Boolean(state.audio?.speaking));
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

  #bindInput() {
    const { input, send, cancel, capture } = this.elements;
    input.addEventListener("compositionstart", () => {
      this.composing = true;
    });
    input.addEventListener("compositionend", () => {
      this.composing = false;
    });
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
