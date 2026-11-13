import { createIcon } from "../core/icons.js";

export function createImageViewer({ document, load, onError }) {
  const dialog = document.createElement("dialog");
  dialog.className = "history-image-viewer";
  dialog.setAttribute("aria-label", "图片预览");
  const header = document.createElement("div");
  header.className = "image-viewer-header";
  const toolbar = document.createElement("div");
  toolbar.className = "image-viewer-toolbar";
  toolbar.setAttribute("role", "toolbar");
  toolbar.setAttribute("aria-label", "图片缩放");
  const count = document.createElement("span");
  count.className = "image-viewer-count";
  count.setAttribute("aria-live", "polite");
  header.append(count);
  function control(parent, label, content, action) {
    const button = document.createElement("button");
    button.type = "button";
    button.setAttribute("aria-label", label);
    button.title = label;
    button.append(content);
    button.addEventListener("click", action);
    parent.append(button);
    return button;
  }
  const original = control(header, "查看原图", "查看原图", () => show(activeIndex, "original"));
  const close = control(header, "关闭图片预览", createIcon(document, "x"), () => dialog.close());
  close.autofocus = true;
  const previous = control(dialog, "上一张图片", createIcon(document, "chevron-right"), () => show(activeIndex - 1));
  previous.className = "image-viewer-previous";
  const next = control(dialog, "下一张图片", createIcon(document, "chevron-right"), () => show(activeIndex + 1));
  next.className = "image-viewer-next";
  const smaller = control(toolbar, "缩小图片", "−", () => setZoom(zoom / 1.25));
  const actual = control(toolbar, "实际尺寸", "100%", () => setZoom(1 / fitScale()));
  const larger = control(toolbar, "放大图片", createIcon(document, "plus"), () => setZoom(zoom * 1.25));
  const divider = document.createElement("span");
  divider.className = "image-viewer-divider";
  toolbar.append(divider);
  const fit = control(toolbar, "适应窗口", createIcon(document, "scan-line"), () => resetZoom());
  const footer = document.createElement("div");
  footer.className = "image-viewer-footer";
  footer.append(toolbar);
  const viewport = document.createElement("div");
  viewport.className = "image-viewer-viewport";
  const canvas = document.createElement("div");
  canvas.className = "image-viewer-canvas";
  let full = document.createElement("img");
  full.draggable = false;
  full.alt = "";
  full.hidden = true;
  let slide = document.createElement("div");
  slide.className = "image-viewer-slide";
  slide.append(full);
  const message = document.createElement("p");
  message.className = "image-viewer-message";
  message.setAttribute("role", "status");
  message.hidden = true;
  canvas.append(slide, message);
  viewport.append(canvas);
  dialog.append(header, viewport, footer);
  document.body.append(dialog);
  let activeImages = [];
  let activeIndex = 0;
  let displayedIndex = -1;
  let zoom = 1;
  let offset = { x: 0, y: 0 };
  let dragged = false;
  let revision = 0;
  let opener = null;
  let drag = null;
  const previews = new Map();
  let zoomMotion = null;
  let slideMotions = [];
  let outgoing = null;

  function releasePreview(entry) {
    entry.disposed = true;
    entry.element.removeAttribute("src");
    if (entry.url) URL.revokeObjectURL(entry.url);
  }

  function prepare(index, variant) {
    const key = `${index}:${variant}`;
    if (previews.has(key)) return previews.get(key);
    const element = document.createElement("img");
    element.draggable = false;
    element.alt = `图片 ${index + 1}`;
    const entry = { index, element, url: null, disposed: false };
    entry.ready = (async () => {
      const blob = await load(activeImages[index], variant);
      if (entry.disposed) return;
      entry.url = URL.createObjectURL(blob);
      element.src = entry.url;
      await element.decode();
    })();
    previews.set(key, entry);
    return entry;
  }

  function prunePreviews() {
    for (const [key, entry] of previews) {
      if (entry.element === full || outgoing?.contains(entry.element)
          || Math.abs(entry.index - activeIndex) <= 1) continue;
      releasePreview(entry);
      previews.delete(key);
    }
  }

  function preloadNeighbours() {
    for (const index of [activeIndex - 1, activeIndex + 1]) {
      const image = activeImages[index];
      // 预加载失败由实际查看时的 await 呈现，未查看的图片不弹错误。
      if (image) void prepare(index, image.preview ? "preview" : "original").ready.catch(() => {});
    }
    prunePreviews();
  }

  function finishSlides() {
    for (const motion of slideMotions) motion.cancel();
    slideMotions = [];
    outgoing?.remove();
    outgoing = null;
  }

  function stopZoom(freeze = false) {
    if (!zoomMotion) return;
    if (freeze) {
      const rect = full.getBoundingClientRect(), bounds = slide.getBoundingClientRect();
      zoom = rect.width / full.naturalWidth / fitScale();
      offset = { x: rect.left + rect.width / 2 - bounds.left - bounds.width / 2,
        y: rect.top + rect.height / 2 - bounds.top - bounds.height / 2 };
    }
    zoomMotion.cancel();
    zoomMotion = null;
    if (freeze) sizeImage();
  }

  function stopDrag() {
    if (drag && viewport.hasPointerCapture(drag.id)) viewport.releasePointerCapture(drag.id);
    drag = null;
    dragged = false;
    viewport.classList.remove("is-dragging");
  }

  function clearPreview() {
    stopZoom();
    finishSlides();
    stopDrag();
    viewport.classList.remove("is-zoomed");
    full.hidden = true;
    full.removeAttribute("src");
    for (const entry of previews.values()) releasePreview(entry);
    previews.clear();
    displayedIndex = -1;
    message.hidden = true;
    dialog.removeAttribute("aria-busy");
  }

  function fitScale() {
    return Math.min(1, viewport.clientWidth / full.naturalWidth,
      viewport.clientHeight / full.naturalHeight);
  }

  function sizeImage(animate = false) {
    if (!full.naturalWidth || full.hidden) return;
    const before = animate ? getComputedStyle(full).transform : null;
    stopZoom();
    const scale = fitScale() * zoom;
    const limitX = Math.max(0, (full.naturalWidth * scale - viewport.clientWidth) / 2);
    const limitY = Math.max(0, (full.naturalHeight * scale - viewport.clientHeight) / 2);
    offset.x = Math.max(-limitX, Math.min(limitX, offset.x));
    offset.y = Math.max(-limitY, Math.min(limitY, offset.y));
    full.style.width = `${full.naturalWidth}px`;
    full.style.height = `${full.naturalHeight}px`;
    full.style.transform = `translate(-50%, -50%) translate(${offset.x}px, ${offset.y}px) scale(${scale})`;
    if (animate) {
      const motion = full.animate([{ transform: before }, { transform: full.style.transform }],
        { duration: 180, easing: "cubic-bezier(.2, .7, .2, 1)" });
      zoomMotion = motion;
      motion.finished.then(() => {
        if (zoomMotion === motion) zoomMotion = null;
      }, () => {});
    }
    viewport.classList.toggle("is-zoomed", limitX > 0 || limitY > 0);
    actual.textContent = `${Math.round(scale * 1000) / 10}%`;
    fit.setAttribute("aria-pressed", String(zoom === 1));
    smaller.disabled = zoom <= 1 / 8;
    larger.disabled = zoom >= 8 / fitScale();
  }

  function setZoom(value, point) {
    if (full.hidden || !full.naturalWidth) return;
    const bounds = viewport.getBoundingClientRect();
    const x = point ? point.clientX - bounds.left - bounds.width / 2 : 0;
    const y = point ? point.clientY - bounds.top - bounds.height / 2 : 0;
    const nextZoom = Math.max(1 / 8, Math.min(8 / fitScale(), value));
    // 保留鼠标所指的图片位置，只有碰到图片边缘时才收敛位移。
    offset.x = x - (x - offset.x) * nextZoom / zoom;
    offset.y = y - (y - offset.y) * nextZoom / zoom;
    zoom = nextZoom;
    sizeImage(true);
  }

  function resetZoom() {
    zoom = 1;
    offset = { x: 0, y: 0 };
    sizeImage(true);
  }

  async function show(index, requestedVariant) {
    if (index < 0 || index >= activeImages.length) return;
    activeIndex = index;
    const current = ++revision;
    const image = activeImages[index];
    const variant = requestedVariant || (image.preview ? "preview" : "original");
    message.hidden = true;
    dialog.setAttribute("aria-busy", "true");
    original.disabled = true;
    previous.hidden = next.hidden = activeImages.length < 2;
    previous.disabled = index === 0;
    next.disabled = index === activeImages.length - 1;
    if (full.hidden) {
      actual.textContent = "—";
      count.textContent = `${index + 1} / ${activeImages.length}`;
      original.hidden = true;
      smaller.disabled = larger.disabled = fit.disabled = actual.disabled = true;
    }
    const entry = prepare(index, variant);
    prunePreviews();
    try {
      await entry.ready;
      if (current !== revision || !dialog.open) return;
      const direction = Math.sign(index - displayedIndex);
      const hadImage = !full.hidden;
      const start = getComputedStyle(slide).transform;
      finishSlides();
      stopZoom(true);
      stopDrag();
      outgoing = slide;
      outgoing.setAttribute("aria-hidden", "true");
      slide = document.createElement("div");
      slide.className = "image-viewer-slide";
      full = entry.element;
      slide.append(full);
      canvas.prepend(slide);
      full.hidden = false;
      zoom = 1;
      offset = { x: 0, y: 0 };
      displayedIndex = index;
      message.hidden = true;
      dialog.removeAttribute("aria-busy");
      count.textContent = `${index + 1} / ${activeImages.length}`;
      original.hidden = !image.preview || variant === "original";
      fit.disabled = actual.disabled = false;
      original.disabled = false;
      sizeImage();
      if (hadImage && direction) {
        const distance = direction * viewport.clientWidth;
        const options = { duration: 280, easing: "cubic-bezier(.22, 1, .36, 1)" };
        slideMotions = [
          outgoing.animate([{ transform: start }, { transform: `translateX(${-distance}px)` }], options),
          slide.animate([{ transform: `translateX(${distance}px)` }, { transform: "translateX(0)" }], options),
        ];
        const incoming = slide;
        Promise.all(slideMotions.map(motion => motion.finished)).then(() => {
          if (slide !== incoming) return;
          finishSlides();
          prunePreviews();
        }, () => {});
      } else {
        finishSlides();
      }
      preloadNeighbours();
    } catch (error) {
      if (current !== revision || !dialog.open) return;
      activeIndex = displayedIndex < 0 ? index : displayedIndex;
      original.disabled = full.hidden;
      previous.disabled = activeIndex === 0;
      next.disabled = activeIndex === activeImages.length - 1;
      dialog.removeAttribute("aria-busy");
      message.hidden = !full.hidden;
      message.textContent = "图片无法读取";
      releasePreview(entry);
      previews.delete(`${index}:${variant}`);
      onError(error);
    }
  }

  dialog.addEventListener("close", () => {
    revision += 1;
    clearPreview();
    activeImages = [];
    const returnTo = opener?.isConnected ? opener
      : document.querySelector(`.history-image-thumbnail[data-image-id="${opener?.dataset.imageId}"]`);
    returnTo?.focus({ preventScroll: true });
    opener = null;
  });
  dialog.addEventListener("keydown", event => {
    if (event.key === "ArrowLeft") { event.preventDefault(); void show(activeIndex - 1); }
    if (event.key === "ArrowRight") { event.preventDefault(); void show(activeIndex + 1); }
    if (event.key === "+" || event.key === "=") { event.preventDefault(); setZoom(zoom * 1.25); }
    if (event.key === "-") { event.preventDefault(); setZoom(zoom / 1.25); }
    if (event.key === "0") { event.preventDefault(); resetZoom(); }
    if (event.key === "1") { event.preventDefault(); setZoom(1 / fitScale()); }
  });
  viewport.addEventListener("dblclick", event => {
    // 拖动捕获会把放大后的双击目标改为 viewport。
    if (dragged || full.hidden || (event.target !== full && zoom <= 1)) return;
    if (zoom === 1) setZoom(Math.max(2, 1 / fitScale()), event);
    else resetZoom();
  });
  viewport.addEventListener("wheel", event => {
    event.preventDefault();
    const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? viewport.clientHeight : 1;
    setZoom(zoom * Math.exp(-event.deltaY * unit * .002), event);
  }, { passive: false });
  viewport.addEventListener("pointerdown", event => {
    if (event.button !== 0) return;
    stopZoom(true);
    dragged = false;
    if (!viewport.classList.contains("is-zoomed")) return;
    drag = { id: event.pointerId, x: event.clientX, y: event.clientY, offset: { ...offset } };
    viewport.setPointerCapture(event.pointerId);
    viewport.classList.add("is-dragging");
    event.preventDefault();
  });
  viewport.addEventListener("pointermove", event => {
    if (!drag || drag.id !== event.pointerId) return;
    const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
    if (Math.hypot(dx, dy) > 3) dragged = true;
    offset = { x: drag.offset.x + dx, y: drag.offset.y + dy };
    sizeImage();
  });
  viewport.addEventListener("pointerup", event => {
    if (viewport.hasPointerCapture(event.pointerId)) viewport.releasePointerCapture(event.pointerId);
  });
  viewport.addEventListener("lostpointercapture", () => {
    drag = null;
    viewport.classList.remove("is-dragging");
  });
  viewport.addEventListener("click", event => {
    if (!dragged && zoom <= 1 && [viewport, canvas, slide].includes(event.target)) dialog.close();
  });
  const resize = new ResizeObserver(() => { finishSlides(); sizeImage(); });
  resize.observe(viewport);

  return {
    open(images, index, button) {
      opener = button;
      activeImages = images;
      dialog.showModal();
      void show(index);
    },
    close() {
      revision += 1;
      clearPreview();
      dialog.close();
    },
    dispose() {
      this.close();
      resize.disconnect();
      dialog.remove();
    },
  };
}
