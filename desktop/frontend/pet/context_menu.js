export const PRODUCT_MENU_ACTIONS = Object.freeze({
  visibility: "sakura.pet.visibility.toggle",
  subtitleZh: "sakura.chat.subtitle.zh",
  subtitleJa: "sakura.chat.subtitle.ja",
  subtitleBilingual: "sakura.chat.subtitle.bilingual",
  subtitleBilingualJa: "sakura.chat.subtitle.bilingual_ja",
  japaneseOriginal: "sakura.chat.japanese-original.toggle",
  topmost: "sakura.pet.topmost.toggle",
  history: "sakura.history.open",
  runtimeLog: "sakura.runtime-log.open",
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
    this.submenu = menu.querySelector("#pet-language-menu");
    this.submenuTrigger = menu.querySelector("[data-menu-submenu]");
    this.languageGroup = menu.querySelector(".pet-context-menu__language");
    this.submenuCloseTimer = null;
    this.disposed = false;
    this.pendingAction = false;
    this.openRevision = 0;
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
      if (this.submenu && !this.submenu.hidden) {
        this.closeSubmenu(true);
        return;
      }
      this.close().catch(() => {});
    };
    this.boundWindowBlur = () => this.close().catch(() => {});
    this.boundMenuClick = (event) => {
      if (event.target.closest?.("[data-menu-submenu]")) {
        this.openSubmenu(true);
        return;
      }
      const item = event.target.closest?.("[data-menu-action]");
      if (!item || item.disabled || item.getAttribute("aria-disabled") === "true") return;
      this.activate(item.dataset.menuAction).catch(() => {});
    };
    this.boundMenuKeyDown = (event) => this.handleMenuKeyDown(event);
    this.boundLanguageEnter = () => this.openSubmenu();
    // Allow a diagonal pointer path from the parent row to the lower language choices.
    this.boundLanguageLeave = () => {
      this.submenuCloseTimer = this.window.setTimeout(() => this.closeSubmenu(), 200);
    };
    this.languageGroup?.addEventListener("pointerenter", this.boundLanguageEnter);
    this.languageGroup?.addEventListener("pointerleave", this.boundLanguageLeave);
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
      if (["menuitemcheckbox", "menuitemradio"].includes(item.getAttribute?.("role"))) {
        item.setAttribute("aria-checked", String(checked.has(item.dataset.menuAction)));
      }
    }
    for (const item of this.menu.querySelectorAll("[data-menu-unavailable]")) {
      item.disabled = true;
      item.setAttribute("aria-disabled", "true");
    }
    const unavailableReason = this.menu.querySelector("#pet-context-menu-unavailable-reason");
    if (unavailableReason) unavailableReason.textContent = manifest.unavailableReason;
    return manifest;
  }

  async openAt(clientX, clientY, manifest, {
    focusFirst = false, surfaceOffset = [0, 0], contentScale = 1, viewport = null,
  } = {}) {
    if (this.disposed) return;
    const openRevision = ++this.openRevision;
    this.closeSubmenu();
    this.applyManifest(manifest);
    this.menu.classList.remove("is-open");
    if (focusFirst) this.menu.classList.add("is-keyboard-open");
    else if (this.menu.classList.contains?.("is-keyboard-open")) this.menu.classList.remove("is-keyboard-open");
    this.menu.hidden = false;
    this.menu.style.visibility = "hidden";
    this.menu.style.left = "0px";
    this.menu.style.top = "0px";
    const bounds = this.menu.getBoundingClientRect();
    const offset = [viewport?.x || 0, viewport?.y || 0];
    const menuWidth = this.menu.offsetWidth || bounds.width;
    // Reserve the submenu in the native crop before showing the menu, so hovering never
    // races a native resize or moves the portrait. Both panels fit within this one surface.
    let submenuWidth = 0;
    if (this.submenu) {
      this.submenu.hidden = false;
      submenuWidth = this.submenu.offsetWidth + 6;
      this.submenu.hidden = true;
    }
    const viewportWidth = viewport?.width ?? this.window.innerWidth;
    const opensLeft = clientX - offset[0] + menuWidth + submenuWidth + 20 > viewportWidth;
    if (this.submenu) this.submenu.dataset.side = opensLeft ? "left" : "right";
    const localPosition = clampMenuPosition(
      clientX - offset[0] - (opensLeft ? submenuWidth : 0),
      clientY - offset[1],
      menuWidth + submenuWidth,
      this.menu.offsetHeight || bounds.height,
      viewport || { width: this.window.innerWidth, height: this.window.innerHeight },
    );
    const position = { x: localPosition.x + offset[0], y: localPosition.y + offset[1] };
    this.menu.style.left = `${position.x + (opensLeft ? submenuWidth : 0)}px`;
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
        Math.max(1, Math.ceil((menuWidth + submenuWidth) / scale)),
        Math.max(1, Math.ceil((this.menu.offsetHeight || bounds.height) / scale)),
      ],
    });
    if (this.disposed || this.menu.hidden || openRevision !== this.openRevision) return;
    this.menu.style.visibility = "visible";
    // Flush the class removal so reopening an already-visible menu replays
    // the entrance animation at its new position.
    void this.menu.offsetWidth;
    this.menu.classList.add("is-open");
    if (focusFirst) this.enabledItems()[0]?.focus({ preventScroll: true });
  }

  enabledItems() {
    if (!this.submenu) return Array.from(this.menu.querySelectorAll("[data-menu-action]:not(:disabled)"));
    if (!this.submenu.hidden && this.submenu.contains(this.document.activeElement)) {
      return Array.from(this.submenu.querySelectorAll("[data-menu-action]:not(:disabled)"));
    }
    return Array.from(this.menu.querySelectorAll("[data-menu-action]:not(:disabled), [data-menu-submenu]"))
      .filter((item) => !this.submenu.contains(item));
  }

  openSubmenu(focus = false) {
    if (!this.submenu || this.menu.hidden) return;
    this.window.clearTimeout(this.submenuCloseTimer);
    this.submenuCloseTimer = null;
    this.submenu.hidden = false;
    this.submenuTrigger.setAttribute("aria-expanded", "true");
    if (focus) {
      const item = this.submenu.querySelector('[aria-checked="true"]:not(:disabled)')
        || this.submenu.querySelector("button:not(:disabled)");
      item?.focus({ preventScroll: true });
    }
  }

  closeSubmenu(focus = false) {
    if (!this.submenu) return;
    this.window.clearTimeout(this.submenuCloseTimer);
    this.submenuCloseTimer = null;
    const hadFocus = this.submenu.contains(this.document.activeElement);
    this.submenu.hidden = true;
    this.submenuTrigger.setAttribute("aria-expanded", "false");
    if (focus || hadFocus) this.submenuTrigger.focus({ preventScroll: true });
  }

  handleMenuKeyDown(event) {
    if (this.menu.hidden) return;
    this.menu.classList.add("is-keyboard-open");
    if (event.key === "ArrowRight" && event.target === this.submenuTrigger) {
      event.preventDefault();
      this.openSubmenu(true);
      return;
    }
    if (event.key === "ArrowLeft" && this.submenu && !this.submenu.hidden) {
      event.preventDefault();
      this.closeSubmenu(true);
      return;
    }
    if (event.key === "Enter" || event.key === " ") {
      const item = event.target.closest?.("[data-menu-action]:not(:disabled), [data-menu-submenu]");
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
    if (this.submenu && !this.submenu.contains(items[index])) this.closeSubmenu();
  }

  hide() {
    this.openRevision += 1;
    if (this.menu.hidden) return false;
    const focusedItem = this.document.activeElement;
    if (focusedItem && this.menu.contains(focusedItem)) focusedItem.blur?.();
    this.menu.hidden = true;
    this.closeSubmenu();
    this.menu.classList.remove("is-open", "is-keyboard-open");
    this.menu.style.visibility = "";
    return true;
  }

  async close() {
    if (!this.hide()) return;
    await this.restoreNativeSurface();
  }

  async dismissForSurfaceTransition() {
    // A native surface mutation must invalidate a menu which is still opening as well as an
    // already-visible menu. The native close command is idempotent, so invoke it even when the
    // DOM is hidden while set_pet_context_menu_surface or a menu action is still in flight.
    this.hide();
    await this.restoreNativeSurface();
  }

  async restoreNativeSurface() {
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
    this.languageGroup?.removeEventListener("pointerenter", this.boundLanguageEnter);
    this.languageGroup?.removeEventListener("pointerleave", this.boundLanguageLeave);
  }
}
