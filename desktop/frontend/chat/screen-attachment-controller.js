import { createIcon } from "../core/icons.js";
export function createScreenAttachmentController({
  composer,
  toggle,
  menu,
  captureItem,
  attachmentList,
  invoke,
  onError = () => {},
  onAttachmentsChanged = () => {},
  onStateChanged = () => {},
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
  if (!composer || !toggle || !menu || !captureItem || !attachmentList
      || typeof invoke !== "function") {
    throw new Error("screen attachment controller requires complete dependencies");
  }
  const attachmentLimit = 6;
  let open = false;
  let capturing = false;
  let submitting = false;
  let attachmentId = null;
  let attachments = [];
  const removing = new Set();
  let layoutRevision = 0;

  function releaseAttachment(value) {
    if (!value) return;
    try {
      void Promise.resolve(invoke("release_screen_attachment", {
        payload: { attachmentId: value },
      })).catch(() => {});
    } catch {
      // A generation change can tear down Core before the best-effort release arrives.
    }
  }

  function renderControls() {
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
    toggle.dataset.attached = attachments.length ? "true" : "false";
    toggle.dataset.attachmentCount = String(attachments.length);
    toggle.disabled = capturing || submitting;
    captureItem.disabled = capturing || submitting || attachments.length >= attachmentLimit;
    const detail = attachments.length ? `，已附加 ${attachments.length} 张截图` : "";
    toggle.setAttribute("aria-label", `添加附件${detail}`);
    toggle.title = `添加附件${detail}`;
    captureItem.title = attachments.length >= attachmentLimit
      ? `每条消息最多附加 ${attachmentLimit} 张截图`
      : "框选屏幕区域并随消息发送";
    renderAttachmentList();
    onStateChanged(Object.freeze({
      open,
      capturing,
      submitting,
      attachmentCount: attachments.length,
      busy: open || capturing || submitting || attachments.length > 0,
    }));
  }

  function renderAttachmentList() {
    attachmentList.hidden = attachments.length === 0;
    attachmentList.replaceChildren();
    const doc = attachmentList.ownerDocument;
    attachments.forEach((attachment, index) => {
      const chip = doc.createElement("div");
      chip.className = "composer-attachment-chip";
      chip.setAttribute("role", "listitem");

      const copy = doc.createElement("span");
      copy.className = "composer-attachment-chip__copy";
      const name = doc.createElement("span");
      name.className = "composer-attachment-chip__name";
      name.textContent = `截图 ${index + 1}`;
      const size = doc.createElement("span");
      size.className = "composer-attachment-chip__size";
      size.textContent = `${attachment.width}×${attachment.height}`;
      copy.append(name, size);

      const remove = doc.createElement("button");
      remove.type = "button";
      remove.className = "composer-attachment-chip__remove";
      remove.disabled = submitting || removing.has(attachment.itemId);
      remove.setAttribute(
        "aria-label",
        `移除截图 ${index + 1}（${attachment.width} × ${attachment.height}）`,
      );
      remove.append(createIcon(doc, "x"));
      remove.addEventListener("click", () => { void removeAttachment(attachment.itemId); });
      chip.append(copy, remove);
      attachmentList.append(chip);
    });
  }

  function publishAttachmentCount() {
    composer.dataset.attachmentCount = String(attachments.length);
    onAttachmentsChanged(attachments.length);
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
    if (capturing || submitting) return false;
    if (attachments.length >= attachmentLimit) {
      onError(`每条消息最多附加 ${attachmentLimit} 张截图。`);
      return false;
    }
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

  async function removeAttachment(itemId) {
    if (submitting || removing.has(itemId) || !attachmentId) return false;
    const target = attachments.find((item) => item.itemId === itemId);
    if (!target) return false;
    removing.add(itemId);
    renderControls();
    try {
      const result = await invoke("remove_screen_attachment_item", {
        payload: { attachmentId, itemId },
      });
      if (result?.accepted !== true || result?.attachmentId !== attachmentId
          || result?.itemId !== itemId || result?.count !== attachments.length - 1) {
        throw new Error("SCREEN_ATTACHMENT_REMOVE_REJECTED");
      }
      attachments = attachments.filter((item) => item.itemId !== itemId);
      if (!attachments.length) attachmentId = null;
      removing.delete(itemId);
      publishAttachmentCount();
      renderControls();
      return true;
    } catch {
      removing.delete(itemId);
      renderControls();
      onError("无法移除这张截图，请重试。");
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
  composer.dataset.attachmentCount = "0";
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
    busy: () => open || capturing || submitting || attachments.length > 0,
    attachmentId: () => attachmentId,
    attachments: () => attachments.map((item) => ({ ...item })),
    removeAttachment,
    handleAttached(value) {
      const nextAttachmentId = String(value?.attachmentId || "");
      const itemId = String(value?.itemId || "");
      const width = Number(value?.width);
      const height = Number(value?.height);
      const count = Number(value?.count);
      if (!/^screen-[0-9a-f]{32}$/.test(nextAttachmentId)
          || !/^shot-[0-9a-f]{32}$/.test(itemId)
          || !Number.isSafeInteger(width) || !Number.isSafeInteger(height)
          || width <= 0 || height <= 0 || count !== attachments.length + 1
          || count > attachmentLimit || attachments.some((item) => item.itemId === itemId)
          || (attachmentId !== null && attachmentId !== nextAttachmentId)) return false;
      attachmentId = nextAttachmentId;
      attachments = [...attachments, Object.freeze({ itemId, width, height })];
      capturing = false;
      publishAttachmentCount();
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
    setSubmitting(value) {
      submitting = Boolean(value) && attachmentId !== null;
      renderControls();
    },
    markSent(value) {
      if (!attachmentId || attachmentId !== value) return false;
      attachmentId = null;
      attachments = [];
      removing.clear();
      submitting = false;
      publishAttachmentCount();
      renderControls();
      return true;
    },
    invalidate() {
      releaseAttachment(attachmentId);
      attachmentId = null;
      attachments = [];
      removing.clear();
      capturing = false;
      submitting = false;
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
      publishAttachmentCount();
      renderControls();
    },
  });
}
