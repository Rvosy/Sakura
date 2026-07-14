const INTERACTIVE_CONTEXT_SELECTOR = [
  "button",
  "input",
  "textarea",
  "select",
  "option",
  "details",
  "summary",
  "a[href]",
  "[contenteditable]",
  "[role='button']",
  "#input-card",
  "#tool-confirmation",
  ".pet-context-menu",
].join(", ");

const ACTION_COMMANDS = {
  hide: "hide",
  history: "history",
  diagnostics: "diagnostics",
  settings: "settings",
  quit: "quit",
};

export function clampMenuPosition(clientX, clientY, menuWidth, menuHeight, viewport, margin = 8) {
  const width = Math.max(0, Number(menuWidth) || 0);
  const height = Math.max(0, Number(menuHeight) || 0);
  const viewportWidth = Math.max(0, Number(viewport?.width) || 0);
  const viewportHeight = Math.max(0, Number(viewport?.height) || 0);
  const maximumX = Math.max(margin, viewportWidth - width - margin);
  const maximumY = Math.max(margin, viewportHeight - height - margin);
  return {
    x: Math.min(Math.max(margin, Number(clientX) || 0), maximumX),
    y: Math.min(Math.max(margin, Number(clientY) || 0), maximumY),
  };
}

export function isInteractiveContextTarget(target) {
  return Boolean(target?.closest?.(INTERACTIVE_CONTEXT_SELECTOR));
}

export class PetContextMenu {
  constructor({
    root,
    menu,
    invoke,
    setStatus = () => {},
    onSubtitleLanguageChange = () => {},
    documentRef = document,
    windowRef = window,
  }) {
    this.root = root;
    this.menu = menu;
    this.invoke = invoke;
    this.setStatus = setStatus;
    this.onSubtitleLanguageChange = onSubtitleLanguageChange;
    this.document = documentRef;
    this.window = windowRef;
    this.preferencesReady = false;
    this.backendAvailable = false;
    this.pendingAction = null;
    this.preferences = {
      subtitleLanguage: "zh",
      chineseSubtitles: true,
      freeAccessEnabled: true,
      alwaysOnTopEnabled: false,
    };
    this.items = new Map(
      Array.from(menu.querySelectorAll("[data-menu-action]"), (item) => [
        item.dataset.menuAction,
        item,
      ]),
    );
    this.#bind();
    this.#render();
  }

  applyBootstrap(bootstrap) {
    this.#applyPreferenceState({
      subtitleLanguage:
        bootstrap?.preferences?.subtitleLanguage || bootstrap?.subtitle?.language || "zh",
      chineseSubtitles: bootstrap?.preferences?.chineseSubtitles,
      freeAccessEnabled: bootstrap?.preferences?.freeAccessEnabled,
      alwaysOnTopEnabled: bootstrap?.preferences?.alwaysOnTopEnabled,
    });
    this.preferencesReady = true;
    this.backendAvailable = true;
    this.#render();
  }

  setBackendAvailable(available) {
    this.backendAvailable = Boolean(available);
    this.#render();
  }

  openAt(clientX, clientY) {
    this.menu.hidden = false;
    this.menu.classList.add("is-open");
    this.menu.style.visibility = "hidden";
    this.menu.style.left = "0px";
    this.menu.style.top = "0px";
    const bounds = this.menu.getBoundingClientRect();
    const position = clampMenuPosition(
      clientX,
      clientY,
      this.menu.offsetWidth || bounds.width,
      this.menu.offsetHeight || bounds.height,
      {
        width: this.window.innerWidth,
        height: this.window.innerHeight,
      },
    );
    this.menu.style.left = `${position.x}px`;
    this.menu.style.top = `${position.y}px`;
    this.menu.style.visibility = "visible";
    const firstItem = this.menu.querySelector('[role^="menuitem"]:not(:disabled)');
    firstItem?.focus({ preventScroll: true });
  }

  close() {
    if (this.menu.hidden) return;
    this.menu.hidden = true;
    this.menu.classList.remove("is-open");
    this.menu.style.visibility = "";
  }

  #bind() {
    this.document.addEventListener("contextmenu", (event) => {
      event.preventDefault();
      if (!this.root.contains(event.target) || isInteractiveContextTarget(event.target)) {
        this.close();
        return;
      }
      this.openAt(event.clientX, event.clientY);
    });
    this.document.addEventListener(
      "pointerdown",
      (event) => {
        if (!this.menu.hidden && !this.menu.contains(event.target)) this.close();
      },
      true,
    );
    this.document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape" || this.menu.hidden) return;
      event.preventDefault();
      this.close();
    });
    this.window.addEventListener("blur", () => this.close());
    this.menu.addEventListener("click", (event) => {
      const item = event.target.closest?.("[data-menu-action]");
      if (!item || item.disabled) return;
      this.#activate(item.dataset.menuAction).catch(() => {});
    });
  }

  async #activate(action) {
    this.close();
    if (this.pendingAction) return;
    this.pendingAction = action;
    this.#render();
    try {
      if (action === "subtitle") {
        const language = this.preferences.chineseSubtitles ? "ja" : "zh";
        const state = await this.invoke("set_pet_subtitle_language", { language });
        this.#applyPreferenceState(state);
        this.onSubtitleLanguageChange(this.preferences.subtitleLanguage);
      } else if (action === "free-access") {
        const state = await this.invoke("set_pet_free_access", {
          enabled: !this.preferences.freeAccessEnabled,
        });
        this.#applyPreferenceState(state);
      } else if (action === "always-on-top") {
        const state = await this.invoke("set_pet_always_on_top", {
          enabled: !this.preferences.alwaysOnTopEnabled,
        });
        this.#applyPreferenceState(state);
      } else {
        await this.invoke("pet_menu_action", { action: ACTION_COMMANDS[action] });
      }
    } catch (error) {
      this.setStatus(`桌宠菜单操作失败：${error}`, "error");
      throw error;
    } finally {
      this.pendingAction = null;
      this.#render();
    }
  }

  #applyPreferenceState(state) {
    const preferences = state?.preferences || state || {};
    const subtitleLanguage =
      preferences.subtitleLanguage === "ja" || preferences.chineseSubtitles === false
        ? "ja"
        : "zh";
    this.preferences = {
      subtitleLanguage,
      chineseSubtitles:
        typeof preferences.chineseSubtitles === "boolean"
          ? preferences.chineseSubtitles
          : subtitleLanguage === "zh",
      freeAccessEnabled:
        typeof preferences.freeAccessEnabled === "boolean"
          ? preferences.freeAccessEnabled
          : this.preferences.freeAccessEnabled,
      alwaysOnTopEnabled:
        typeof preferences.alwaysOnTopEnabled === "boolean"
          ? preferences.alwaysOnTopEnabled
          : this.preferences.alwaysOnTopEnabled,
    };
  }

  #render() {
    this.items
      .get("subtitle")
      ?.setAttribute("aria-checked", String(this.preferences.chineseSubtitles));
    this.items
      .get("free-access")
      ?.setAttribute("aria-checked", String(this.preferences.freeAccessEnabled));
    this.items
      .get("always-on-top")
      ?.setAttribute("aria-checked", String(this.preferences.alwaysOnTopEnabled));
    for (const item of this.menu.querySelectorAll("[data-requires-preferences]")) {
      const disabled =
        !this.preferencesReady || !this.backendAvailable || this.pendingAction !== null;
      item.disabled = disabled;
      item.setAttribute("aria-disabled", String(disabled));
    }
  }
}
