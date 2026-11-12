import { createIcon } from "../core/icons.js";
import { createImageAlbum } from "./history-image-album.js";

export function createHistoryImages({ document, scroll, invoke, getIdentity, onError }) {
  const thumbnails = new Set();
  const albums = new Set();
  const albumStates = new Map();
  const dialog = document.createElement("dialog");
  dialog.className = "history-image-viewer";
  dialog.setAttribute("aria-label", "图片预览");
  const toolbar = document.createElement("div");
  toolbar.className = "image-viewer-toolbar";
  const count = document.createElement("span");
  count.className = "image-viewer-count";
  count.setAttribute("aria-live", "polite");
  toolbar.append(count);
  function control(label, content, action) {
    const button = document.createElement("button");
    button.type = "button";
    button.setAttribute("aria-label", label);
    button.title = label;
    button.append(content);
    button.addEventListener("click", action);
    toolbar.append(button);
    return button;
  }
  const previous = control("上一张图片", "‹", () => show(activeIndex - 1));
  const next = control("下一张图片", "›", () => show(activeIndex + 1));
  const smaller = control("缩小图片", "−", () => setZoom(zoom / 1.25));
  const fit = control("适应窗口", "适应", () => setZoom(1));
  const larger = control("放大图片", "+", () => setZoom(zoom * 1.25));
  const original = control("查看原图", "原图", () => show(activeIndex, "original"));
  control("关闭图片预览", createIcon(document, "x"), () => dialog.close());
  const viewport = document.createElement("div");
  viewport.className = "image-viewer-viewport";
  const canvas = document.createElement("div");
  canvas.className = "image-viewer-canvas";
  const full = document.createElement("img");
  full.draggable = false;
  full.alt = "";
  const message = document.createElement("p");
  message.className = "image-viewer-message";
  message.setAttribute("role", "status");
  canvas.append(full, message);
  viewport.append(canvas);
  dialog.append(toolbar, viewport);
  document.body.append(dialog);
  let activeImages = [];
  let activeIndex = 0;
  let zoom = 1;
  let revision = 0;
  let opener = null;
  let drag = null;
  let previewUrl = null;

  async function load(image, variant) {
    const { coreGenerationId, characterId } = getIdentity();
    const bytes = await invoke("history_image", { request: {
      coreGenerationId, characterId, entryId: image.entryId, imageId: image.id, variant,
    } });
    return new Blob([new Uint8Array(bytes)], {
      type: variant === "original" ? image.mediaType : "image/webp",
    });
  }

  function clearPreview() {
    full.hidden = true;
    full.removeAttribute("src");
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = null;
  }

  function sizeImage() {
    if (!full.naturalWidth || full.hidden) return;
    const scale = Math.min(1, viewport.clientWidth / full.naturalWidth,
      viewport.clientHeight / full.naturalHeight) * zoom;
    full.style.width = `${full.naturalWidth * scale}px`;
    full.style.height = `${full.naturalHeight * scale}px`;
    viewport.classList.toggle("is-zoomed", zoom > 1);
    fit.textContent = zoom === 1 ? "适应" : `${Math.round(scale * 100)}%`;
    smaller.disabled = zoom <= 1;
    larger.disabled = zoom >= 8;
  }

  function setZoom(value) {
    const x = (viewport.scrollLeft + viewport.clientWidth / 2) / viewport.scrollWidth;
    const y = (viewport.scrollTop + viewport.clientHeight / 2) / viewport.scrollHeight;
    zoom = Math.max(1, Math.min(8, value));
    sizeImage();
    viewport.scrollLeft = x * viewport.scrollWidth - viewport.clientWidth / 2;
    viewport.scrollTop = y * viewport.scrollHeight - viewport.clientHeight / 2;
  }

  async function show(index, requestedVariant) {
    if (index < 0 || index >= activeImages.length) return;
    activeIndex = index;
    const current = ++revision;
    zoom = 1;
    clearPreview();
    const image = activeImages[index];
    const variant = requestedVariant || (image.preview ? "preview" : "original");
    original.hidden = !image.preview || variant === "original";
    original.disabled = true;
    message.hidden = false;
    message.textContent = "正在加载图片…";
    count.textContent = `${index + 1} / ${activeImages.length}`;
    previous.hidden = next.hidden = activeImages.length < 2;
    previous.disabled = index === 0;
    next.disabled = index === activeImages.length - 1;
    smaller.disabled = larger.disabled = fit.disabled = true;
    try {
      const blob = await load(image, variant);
      if (current !== revision || !dialog.open) return;
      previewUrl = URL.createObjectURL(blob);
      full.src = previewUrl;
      await full.decode();
      if (current !== revision || !dialog.open) return;
      full.alt = `图片 ${index + 1}`;
      full.hidden = false;
      message.hidden = true;
      fit.disabled = false;
      original.disabled = false;
      sizeImage();
      viewport.scrollTo(0, 0);
    } catch (error) {
      if (current !== revision || !dialog.open) return;
      message.textContent = "图片无法读取";
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
    if (event.key === "0") setZoom(1);
  });
  viewport.addEventListener("dblclick", event => {
    if (event.target === full) setZoom(zoom === 1 ? 2 : 1);
  });
  viewport.addEventListener("wheel", event => {
    if (!event.ctrlKey) return;
    event.preventDefault();
    setZoom(zoom * (event.deltaY < 0 ? 1.25 : 0.8));
  }, { passive: false });
  viewport.addEventListener("pointerdown", event => {
    if (event.button !== 0 || zoom <= 1) return;
    drag = { x: event.clientX, y: event.clientY, left: viewport.scrollLeft, top: viewport.scrollTop };
    viewport.setPointerCapture(event.pointerId);
    event.preventDefault();
  });
  viewport.addEventListener("pointermove", event => {
    if (!drag) return;
    viewport.scrollLeft = drag.left - event.clientX + drag.x;
    viewport.scrollTop = drag.top - event.clientY + drag.y;
  });
  viewport.addEventListener("lostpointercapture", () => { drag = null; });
  viewport.addEventListener("click", event => {
    if (zoom === 1 && [viewport, canvas].includes(event.target)) dialog.close();
  });
  const resize = new ResizeObserver(sizeImage);
  resize.observe(viewport);

  function gallery(images, { expandable = true } = {}) {
    if (images.length > 1) {
      const key = images[0].entryId;
      if (!albumStates.has(key)) albumStates.set(key, { index: 0, expanded: false });
      const album = createImageAlbum({ document, images, scroll, expandable, state: albumStates.get(key),
        makeThumbnail: (image, index) => makeThumbnail(images, image, index),
      });
      albums.add(album);
      return album.element;
    }
    const group = document.createElement("div");
    group.className = "history-image-gallery is-single";
    const button = makeThumbnail(images, images[0], 0);
    group.append(button);
    return group;
  }

  function makeThumbnail(images, image, index) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "history-image-thumbnail";
    button.dataset.imageId = image.id;
    if (images.length === 1 && image.width && image.height) {
      // 单张图保留完整比例，并限制为 320×320 CSS 像素。
      button.style.aspectRatio = `${image.width} / ${image.height}`;
      button.style.width = `${Math.min(320, 320 * image.width / image.height)}px`;
    }
    button.setAttribute("aria-label", `预览图片 ${index + 1}`);
    const thumbnail = document.createElement("img");
    thumbnail.alt = `图片 ${index + 1}`;
    thumbnail.draggable = false;
    thumbnail.decoding = "async";
    thumbnail.hidden = true;
    const label = document.createElement("span");
    label.textContent = image.thumbnail ? "正在加载…" : "查看图片";
    button.append(thumbnail, label);
    let failure = null;
    let thumbnailUrl = null;
    let disposed = false;
    button.loadImage = async () => {
      if (disposed || !image.thumbnail) return;
      try {
        const blob = await load(image, "thumbnail");
        if (disposed) return;
        thumbnailUrl = URL.createObjectURL(blob);
        thumbnail.src = thumbnailUrl;
        await thumbnail.decode();
        if (disposed) return;
        thumbnail.hidden = false;
        label.hidden = true;
      } catch (error) {
        if (disposed) return;
        failure = error;
        label.textContent = "图片无法读取";
      }
    };
    button.disposeThumbnail = () => {
      disposed = true;
      thumbnail.removeAttribute("src");
      if (thumbnailUrl) URL.revokeObjectURL(thumbnailUrl);
      thumbnails.delete(button);
    };
    button.addEventListener("click", () => {
      if (failure) { onError(failure); return; }
      opener = button;
      activeImages = images;
      dialog.showModal();
      void show(index);
    });
    thumbnails.add(button);
    return button;
  }

  function beginRender(entryIds = new Set()) {
    for (const album of albums) album.dispose();
    albums.clear();
    for (const button of thumbnails) button.disposeThumbnail();
    for (const key of albumStates.keys()) if (!entryIds.has(key)) albumStates.delete(key);
  }

  function reset() {
    revision += 1;
    clearPreview();
    dialog.close();
    beginRender();
  }

  return { gallery, beginRender, async finishRender() {
    for (const album of albums) album.layout();
    // Core 的 fixture worker 有 4 个；提前读取全部图片，但不一次塞满请求队列。
    const pending = [...thumbnails].values();
    await Promise.all(Array.from({ length: 4 }, async () => {
      for (const button of pending) await button.loadImage();
    }));
  }, reset, dispose() {
    reset();
    resize.disconnect();
    dialog.remove();
  } };
}
