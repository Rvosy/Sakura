export function createScreenAttachmentController({
  composer,
  toggle,
  menu,
  captureItem,
  invoke,
  onError = () => {},
  beforeOpen = async () => {},
  openSurface = async () => {},
  closeSurface = async () => {},
  measureSurface = () => null,
  surfaceAnchor = () => (composer.dataset.inputExpanded === "true" ? "above" : "below"),
  requestFrame = (callback) => globalThis.requestAnimationFrame?.(callback) ?? callback(),
  waitForMotion = (element) => {
    if (globalThis.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches) {
      return Promise.resolve();
    }
    return new Promise((resolve) => {
      let settled = false;
      const finish = (event) => {
        if (event && (event.target !== element || event.propertyName !== "transform")) return;
        if (settled) return;
        settled = true;
        element.removeEventListener?.("transitionend", finish);
        resolve();
      };
      element.addEventListener?.("transitionend", finish);
      globalThis.setTimeout(finish, 220);
    });
  },
} = {}) {
  if (!composer || !toggle || !menu || !captureItem || typeof invoke !== "function") {
    throw new Error("screen attachment controller requires complete dependencies");
  }
  let open = false;
  let capturing = false;
  let attachment = null;
  let layoutRevision = 0;

  function releaseAttachment(value) {
    if (!value) return;
    try {
      void Promise.resolve(invoke("release_screen_attachment", {
        payload: { attachmentId: value.attachmentId },
      })).catch(() => {});
    } catch {
      // A generation change can tear down Core before the best-effort release arrives.
    }
  }

  function renderControls() {
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
    toggle.dataset.attached = attachment ? "true" : "false";
    toggle.disabled = capturing;
    captureItem.disabled = capturing;
    const detail = attachment ? `，已附加截图 ${attachment.width} × ${attachment.height}` : "";
    toggle.setAttribute("aria-label", `添加附件${detail}`);
    toggle.title = `添加附件${detail}`;
  }

  function nextPaint() {
    return new Promise((resolve) => requestFrame(() => resolve()));
  }

  async function setOpen(value, { focus = false } = {}) {
    const next = Boolean(value) && !capturing;
    if (next === open) return false;
    open = next;
    const revision = ++layoutRevision;
    renderControls();

    composer.dataset.attachmentMenu = next ? "open" : "closed";
    if (next) {
      await beforeOpen();
      if (revision !== layoutRevision || !open) return false;
      menu.dataset.anchor = surfaceAnchor() === "above" ? "above" : "below";
      menu.hidden = false;
      menu.dataset.open = "false";
      await nextPaint();
      if (revision !== layoutRevision || !open) return false;
      try {
        await openSurface(measureSurface());
      } catch {
        if (revision !== layoutRevision || !open) return false;
        open = false;
        renderControls();
        composer.dataset.attachmentMenu = "closed";
        menu.hidden = true;
        onError("扩展工具暂时无法打开，请重试。");
        return false;
      }
      menu.dataset.open = "true";
      if (focus) captureItem.focus({ preventScroll: true });
      return true;
    }

    menu.dataset.open = "false";
    await waitForMotion(menu);
    if (revision !== layoutRevision || open) return false;
    try {
      await closeSurface();
    } catch {
      onError("扩展工具区域暂时无法关闭，请重试。");
    }
    menu.hidden = true;
    if (focus) toggle.focus({ preventScroll: true });
    return true;
  }

  async function startCapture() {
    if (capturing) return false;
    capturing = true;
    renderControls();
    await setOpen(false);
    try {
      await invoke("start_screen_capture");
      return true;
    } catch {
      capturing = false;
      renderControls();
      onError("无法开始截图，请检查系统屏幕录制权限。");
      return false;
    }
  }

  toggle.addEventListener("click", () => { void setOpen(!open, { focus: !open }); });
  captureItem.addEventListener("click", () => { void startCapture(); });
  menu.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      setOpen(false, { focus: true });
      return;
    }
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
    const items = [...(menu.querySelectorAll?.("button:not(:disabled)") || [])];
    if (!items.length) return;
    event.preventDefault();
    const active = menu.ownerDocument?.activeElement;
    const current = Math.max(0, items.indexOf(active));
    const index = event.key === "Home" ? 0
      : event.key === "End" ? items.length - 1
        : event.key === "ArrowDown" ? (current + 1) % items.length
          : (current - 1 + items.length) % items.length;
    items[index].focus({ preventScroll: true });
  });

  menu.hidden = true;
  menu.dataset.open = "false";
  composer.dataset.attachmentMenu = "closed";
  renderControls();
  return Object.freeze({
    contains(target) {
      return composer.contains(target) || Boolean(menu.contains?.(target));
    },
    close(options) {
      return setOpen(false, options);
    },
    startCapture,
    isOpen: () => open,
    busy: () => open || capturing || attachment !== null,
    attachmentId: () => attachment?.attachmentId || null,
    handleAttached(value) {
      const attachmentId = String(value?.attachmentId || "");
      const width = Number(value?.width);
      const height = Number(value?.height);
      if (!/^screen-[0-9a-f]{32}$/.test(attachmentId) || !Number.isSafeInteger(width)
          || !Number.isSafeInteger(height) || width <= 0 || height <= 0) return false;
      releaseAttachment(attachment);
      attachment = Object.freeze({ attachmentId, width, height });
      capturing = false;
      renderControls();
      return true;
    },
    handleCancelled() {
      capturing = false;
      renderControls();
    },
    handleError(message) {
      capturing = false;
      renderControls();
      onError(String(message || "截图失败，请重试。"));
    },
    markSent(attachmentId) {
      if (!attachment || attachment.attachmentId !== attachmentId) return false;
      attachment = null;
      renderControls();
      return true;
    },
    invalidate() {
      releaseAttachment(attachment);
      attachment = null;
      capturing = false;
      layoutRevision += 1;
      open = false;
      menu.hidden = true;
      menu.dataset.open = "false";
      composer.dataset.attachmentMenu = "closed";
      try {
        void Promise.resolve(closeSurface()).catch(() => {});
      } catch {
        // Native teardown may already have invalidated the surface.
      }
      renderControls();
    },
  });
}
