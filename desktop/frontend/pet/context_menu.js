export const PRODUCT_MENU_ACTIONS = Object.freeze({
  visibility: "sakura.pet.visibility.toggle",
  subtitle: "sakura.chat.subtitle.toggle",
  history: "sakura.history.open",
  settings: "sakura.settings.open",
  exit: "sakura.app.exit",
});

const KNOWN_ACTIONS = Object.freeze(Object.values(PRODUCT_MENU_ACTIONS));

export function clampMenuPosition(clientX, clientY, menuWidth, menuHeight, viewport, margin = 20) {
  const width = Math.max(0, Number(menuWidth) || 0);
  const height = Math.max(0, Number(menuHeight) || 0);
  const viewportWidth = Math.max(0, Number(viewport?.width) || 0);
  const viewportHeight = Math.max(0, Number(viewport?.height) || 0);
  const maximumX = Math.max(margin, viewportWidth - width - margin);
  const maximumY = Math.max(margin, viewportHeight - height - margin);
  return Object.freeze({
    x: Math.min(Math.max(margin, Number(clientX) || 0), maximumX),
    y: Math.min(Math.max(margin, Number(clientY) || 0), maximumY),
  });
}

export function validateProductMenuManifest(value) {
  if (!value || value.schemaVersion !== 1 || !Array.isArray(value.availableActions) || !Array.isArray(value.checkedActions)) {
    throw new Error("PRODUCT_MENU_MANIFEST_INVALID");
  }
  const availableActions = value.availableActions.filter(
    (action, index, actions) =>
      typeof action === "string" && KNOWN_ACTIONS.includes(action) && actions.indexOf(action) === index,
  );
  const checkedActions = value.checkedActions.filter(
    (action, index, actions) =>
      typeof action === "string" && availableActions.includes(action) && actions.indexOf(action) === index,
  );
  return Object.freeze({
    schemaVersion: 1,
    availableActions: Object.freeze(availableActions),
    checkedActions: Object.freeze(checkedActions),
    unavailableReason:
      typeof value.unavailableReason === "string" && value.unavailableReason.trim()
        ? value.unavailableReason
        : "该功能尚未迁移到 Runtime v2",
  });
}

export function moveMenuFocusIndex(currentIndex, itemCount, key) {
  if (!Number.isInteger(itemCount) || itemCount <= 0) return -1;
  if (key === "Home") return 0;
  if (key === "End") return itemCount - 1;
  const current = Number.isInteger(currentIndex) && currentIndex >= 0 ? currentIndex : 0;
  if (key === "ArrowDown") return (current + 1) % itemCount;
  if (key === "ArrowUp") return (current - 1 + itemCount) % itemCount;
  return current;
}

export class PetContextMenu {
  constructor({
    menu,
    invoke,
    onError = () => {},
    beforeSurfaceResize = () => {},
    documentRef = document,
    windowRef = window,
  }) {
    this.menu = menu;
    this.invoke = invoke;
    this.onError = onError;
    this.beforeSurfaceResize = beforeSurfaceResize;
    this.document = documentRef;
    this.window = windowRef;
    this.disposed = false;
    this.pendingAction = false;
    this.boundPointerDown = (event) => {
      if (event.button !== 2 && !this.menu.hidden && !this.menu.contains(event.target)) {
        // The first primary press outside an open menu belongs to menu dismissal. If it reaches a
        // portrait/bubble drag region, the same press also starts AppKit's native move loop while
        // the menu surface is shrinking, which can commit the resize delta as a dragged position.
        event.preventDefault();
        event.stopPropagation();
        this.close().catch(() => {});
      }
    };
    this.boundDocumentKeyDown = (event) => {
      if (event.key !== "Escape" || this.menu.hidden) return;
      event.preventDefault();
      this.close().catch(() => {});
    };
    this.boundWindowBlur = () => this.close().catch(() => {});
    this.boundMenuClick = (event) => {
      const item = event.target.closest?.("[data-menu-action]");
      if (!item || item.disabled || item.getAttribute("aria-disabled") === "true") return;
      this.activate(item.dataset.menuAction).catch(() => {});
    };
    this.boundMenuKeyDown = (event) => this.handleMenuKeyDown(event);
    this.document.addEventListener("pointerdown", this.boundPointerDown, true);
    this.document.addEventListener("keydown", this.boundDocumentKeyDown);
    this.window.addEventListener("blur", this.boundWindowBlur);
    this.menu.addEventListener("click", this.boundMenuClick);
    this.menu.addEventListener("keydown", this.boundMenuKeyDown);
  }

  contains(target) {
    return this.menu.contains(target);
  }

  isOpen() {
    return !this.menu.hidden;
  }

  applyManifest(value) {
    const manifest = validateProductMenuManifest(value);
    const available = new Set(manifest.availableActions);
    const checked = new Set(manifest.checkedActions);
    for (const item of this.menu.querySelectorAll("[data-menu-action]")) {
      const enabled = available.has(item.dataset.menuAction);
      item.disabled = !enabled;
      item.setAttribute("aria-disabled", String(!enabled));
      if (item.getAttribute?.("role") === "menuitemcheckbox") {
        item.setAttribute("aria-checked", String(checked.has(item.dataset.menuAction)));
      }
    }
    for (const item of this.menu.querySelectorAll("[data-menu-unavailable]")) {
      item.disabled = true;
      item.title = manifest.unavailableReason;
      item.setAttribute("aria-disabled", "true");
    }
    const unavailableReason = this.menu.querySelector("#pet-context-menu-unavailable-reason");
    if (unavailableReason) unavailableReason.textContent = manifest.unavailableReason;
    return manifest;
  }

  async openAt(clientX, clientY, manifest, { focusFirst = false, surfaceOffset = [0, 0], contentScale = 1 } = {}) {
    if (this.disposed) return;
    this.applyManifest(manifest);
    this.menu.classList.remove("is-open");
    if (focusFirst) this.menu.classList.add("is-keyboard-open");
    else if (this.menu.classList.contains?.("is-keyboard-open")) this.menu.classList.remove("is-keyboard-open");
    this.menu.hidden = false;
    this.menu.style.visibility = "hidden";
    this.menu.style.left = "0px";
    this.menu.style.top = "0px";
    const bounds = this.menu.getBoundingClientRect();
    const position = clampMenuPosition(
      clientX,
      clientY,
      this.menu.offsetWidth || bounds.width,
      this.menu.offsetHeight || bounds.height,
      { width: this.window.innerWidth, height: this.window.innerHeight },
    );
    this.menu.style.left = `${position.x}px`;
    this.menu.style.top = `${position.y}px`;
    const scale = Number(contentScale);
    if (!Number.isFinite(scale) || scale <= 0) throw new Error("PET_CONTEXT_MENU_SCALE_INVALID");
    // A focused WebView control can lose focus as AppKit resizes the native surface. Clear it
    // before the first native frame mutation so macOS does not combine focus teardown with the
    // menu resize transaction. The callback is intentionally before invoke, not after it.
    this.beforeSurfaceResize();
    await this.invoke("set_pet_context_menu_surface", {
      rect: [
        Math.floor(position.x / scale + Number(surfaceOffset[0] || 0)),
        Math.floor(position.y / scale + Number(surfaceOffset[1] || 0)),
        Math.max(1, Math.ceil((this.menu.offsetWidth || bounds.width) / scale)),
        Math.max(1, Math.ceil((this.menu.offsetHeight || bounds.height) / scale)),
      ],
    });
    if (this.disposed || this.menu.hidden) return;
    this.menu.style.visibility = "visible";
    // Flush the class removal so reopening an already-visible menu replays
    // the entrance animation at its new position.
    void this.menu.offsetWidth;
    this.menu.classList.add("is-open");
    if (focusFirst) this.enabledItems()[0]?.focus({ preventScroll: true });
  }

  enabledItems() {
    return Array.from(this.menu.querySelectorAll("[data-menu-action]:not(:disabled)"));
  }

  handleMenuKeyDown(event) {
    if (this.menu.hidden) return;
    if (event.key === "Enter" || event.key === " ") {
      const item = event.target.closest?.("[data-menu-action]:not(:disabled)");
      if (!item) return;
      event.preventDefault();
      item.click();
      return;
    }
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
    const items = this.enabledItems();
    if (!items.length) return;
    event.preventDefault();
    const index = moveMenuFocusIndex(items.indexOf(this.document.activeElement), items.length, event.key);
    items[index]?.focus({ preventScroll: true });
  }

  hide() {
    if (this.menu.hidden) return false;
    const focusedItem = this.document.activeElement;
    if (focusedItem && this.menu.contains(focusedItem)) focusedItem.blur?.();
    this.menu.hidden = true;
    this.menu.classList.remove("is-open", "is-keyboard-open");
    this.menu.style.visibility = "";
    return true;
  }

  async close() {
    if (!this.hide()) return;
    try {
      await this.invoke("close_pet_context_menu");
    } catch (error) {
      this.onError("桌宠菜单关闭后未能恢复透明区域穿透。", error);
      throw error;
    }
  }

  async activate(actionId) {
    if (this.pendingAction || !KNOWN_ACTIONS.includes(actionId)) return;
    this.pendingAction = true;
    this.hide();
    try {
      await this.invoke("activate_pet_context_menu_action", { actionId });
    } catch (error) {
      this.onError("桌宠菜单操作失败，请稍后重试。", error);
      throw error;
    } finally {
      this.pendingAction = false;
    }
  }

  dispose() {
    if (this.disposed) return;
    this.disposed = true;
    this.hide();
    this.document.removeEventListener("pointerdown", this.boundPointerDown, true);
    this.document.removeEventListener("keydown", this.boundDocumentKeyDown);
    this.window.removeEventListener("blur", this.boundWindowBlur);
    this.menu.removeEventListener("click", this.boundMenuClick);
    this.menu.removeEventListener("keydown", this.boundMenuKeyDown);
  }
}
