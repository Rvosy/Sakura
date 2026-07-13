export class PortraitController {
  constructor({ currentImage, transitionImage, fallback, onNaturalSize = () => {} }) {
    this.currentImage = currentImage;
    this.transitionImage = transitionImage;
    this.fallback = fallback;
    this.onNaturalSize = onNaturalSize;
    this.assets = { default: "", expressions: {} };
    this.currentKey = "default";
    this.transitionToken = 0;
    this.preloaded = new Map();
  }

  setCharacter(character) {
    this.assets = character?.portraits ?? { default: "", expressions: {} };
    this.preloaded.clear();
    this.#preloadAll();
    this.show("default", { immediate: true });
  }

  resolveAsset(key) {
    if (!key || key === "default") return this.assets.default || "";
    return this.assets.expressions?.[key] || this.assets.default || "";
  }

  show(key, { immediate = false } = {}) {
    const source = this.resolveAsset(key);
    if (!source) {
      this.fallback.hidden = false;
      return;
    }
    const token = ++this.transitionToken;
    const finish = (image) => {
      if (token !== this.transitionToken) return;
      this.currentImage.src = source;
      this.currentImage.classList.remove("portrait-image--fading");
      this.transitionImage.classList.remove("portrait-image--visible");
      this.transitionImage.removeAttribute("src");
      this.currentKey = key || "default";
      this.fallback.hidden = true;
      this.onNaturalSize({ width: image.naturalWidth, height: image.naturalHeight });
    };
    const image = this.preloaded.get(source) || new Image();
    if (!this.preloaded.has(source)) {
      image.decoding = "async";
      image.src = source;
      this.preloaded.set(source, image);
    }
    const reveal = () => {
      if (token !== this.transitionToken) return;
      if (immediate || !this.currentImage.getAttribute("src")) {
        finish(image);
        return;
      }
      this.transitionImage.src = source;
      this.transitionImage.classList.add("portrait-image--visible");
      this.currentImage.classList.add("portrait-image--fading");
      window.setTimeout(() => finish(image), 300);
    };
    if (image.complete && image.naturalWidth) reveal();
    else image.addEventListener("load", reveal, { once: true });
    image.addEventListener(
      "error",
      () => {
        if (token === this.transitionToken) this.fallback.hidden = false;
      },
      { once: true },
    );
  }

  showForSegment(segment) {
    this.show(segment?.portrait || segment?.tone || "default");
  }

  #preloadAll() {
    for (const source of [this.assets.default, ...Object.values(this.assets.expressions || {})]) {
      if (!source || this.preloaded.has(source)) continue;
      const image = new Image();
      image.decoding = "async";
      image.src = source;
      this.preloaded.set(source, image);
    }
  }
}
