import { createImageViewer } from "./history-image-viewer.js";
import { createImageAlbum } from "./history-image-album.js";

export function createHistoryImages({ document, scroll, invoke, getIdentity, onError }) {
  const thumbnails = new Set();
  const albums = new Set();
  const albumStates = new Map();
  const waitingReads = [];
  let activeReads = 0;
  const viewer = createImageViewer({ document, load, onError });

  async function load(image, variant) {
    const { coreGenerationId, characterId } = getIdentity();
    // 缩略图、预览和相邻图片预加载共用原有的 4 个读取名额。
    if (activeReads === 4) await new Promise(resolve => waitingReads.push(resolve));
    else activeReads += 1;
    try {
      const bytes = await invoke("history_image", { request: {
        coreGenerationId, characterId, entryId: image.entryId, imageId: image.id, variant,
      } });
      return new Blob([new Uint8Array(bytes)], {
        type: variant === "original" ? image.mediaType : "image/webp",
      });
    } finally {
      const next = waitingReads.shift();
      if (next) next();
      else activeReads -= 1;
    }
  }

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
      viewer.open(images, index, button);
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
    viewer.close();
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
    beginRender();
    viewer.dispose();
  } };
}
