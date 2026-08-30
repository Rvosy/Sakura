export const FIRST_RUN_GUIDE_STEPS = Object.freeze([
  Object.freeze({
    id: "character",
    page: "character",
    selector: "#characterImportButton",
    title: "导入角色",
    description: "在这里导入 .char 角色包。角色包可以包含人设、立绘和语音。",
  }),
  Object.freeze({
    id: "providers",
    page: "providers",
    selector: "#addProviderButton",
    title: "添加供应商",
    description: "在这里填写 API 地址和密钥。模型列表也从这里获取。",
  }),
  Object.freeze({
    id: "models",
    page: "model",
    selector: "#page-model > fieldset.settings-group",
    title: "选择模型",
    description: "每个用途可以单独选模型。",
  }),
]);

const FALLBACK_MODEL_FEATURES = Object.freeze([
  Object.freeze({ label: "对话模型", description: "用于聊天和工具调用。" }),
  Object.freeze({ label: "视觉对话模型", description: "用于图片和屏幕内容。" }),
]);

export function firstRunGuideRequested(search = globalThis.location?.search || "") {
  return new URLSearchParams(search).get("guide") === "first-run";
}

export function nextGuideIndex(current, direction, count = FIRST_RUN_GUIDE_STEPS.length) {
  if (!Number.isSafeInteger(current) || !Number.isSafeInteger(count) || count <= 0) return 0;
  return Math.min(count - 1, Math.max(0, current + direction));
}

export function modelSlotFeatures(root) {
  if (!root?.querySelectorAll) return FALLBACK_MODEL_FEATURES;
  const features = Array.from(root.querySelectorAll(".model-slot-row")).map((row) => ({
    label: row.querySelector?.(".setting-title")?.textContent?.trim() || "模型槽位",
    description: row.querySelector?.(".setting-desc")?.textContent?.trim() || "可单独选择供应商和模型。",
  })).filter(({ label }) => label).slice(0, 4);
  return features.length ? features : FALLBACK_MODEL_FEATURES;
}

export function spotlightGeometry(rect, viewport, padding = 0) {
  if (!rect || !viewport || rect.width <= 0 || rect.height <= 0) return null;
  const left = Math.max(0, rect.left - padding);
  const top = Math.max(0, rect.top - padding);
  const right = Math.min(viewport.width, rect.right + padding);
  const bottom = Math.min(viewport.height, rect.bottom + padding);
  return Object.freeze({
    left,
    top,
    width: Math.max(1, right - left),
    height: Math.max(1, bottom - top),
  });
}

function setRect(element, { left, top, width, height }) {
  element.style.left = `${Math.max(0, left)}px`;
  element.style.top = `${Math.max(0, top)}px`;
  element.style.width = `${Math.max(0, width)}px`;
  element.style.height = `${Math.max(0, height)}px`;
}

export function calloutPosition(spotlight, viewport, calloutSize) {
  const margin = 18;
  const edge = 18;
  const width = Math.min(calloutSize.width || 360, viewport.width - edge * 2);
  const height = Math.min(calloutSize.height || 300, viewport.height - edge * 2);
  if (!spotlight) {
    return {
      left: Math.max(edge, Math.floor((viewport.width - width) / 2)),
      top: Math.max(edge, Math.floor((viewport.height - height) / 2)),
      maxHeight: viewport.height - edge * 2,
    };
  }
  const rightSpace = viewport.width - spotlight.left - spotlight.width - margin;
  const leftSpace = spotlight.left - margin;
  let left;
  if (rightSpace >= width) left = spotlight.left + spotlight.width + margin;
  else if (leftSpace >= width) left = spotlight.left - width - margin;
  else left = Math.min(viewport.width - width - edge, Math.max(edge, spotlight.left));

  const below = spotlight.top + spotlight.height + margin;
  if (rightSpace >= width || leftSpace >= width) {
    return {
      left,
      top: Math.min(viewport.height - height - edge, Math.max(edge, spotlight.top)),
      maxHeight: viewport.height - edge * 2,
    };
  }

  const belowSpace = Math.max(1, viewport.height - edge - below);
  const aboveSpace = Math.max(1, spotlight.top - margin - edge);
  if (belowSpace >= height || belowSpace >= aboveSpace) {
    return { left, top: below, maxHeight: belowSpace };
  }
  return { left, top: edge, maxHeight: aboveSpace };
}

function createButton(document, text, className) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = className;
  button.textContent = text;
  return button;
}

export function createFirstRunGuide({
  document,
  window,
  showPage,
  invoke,
  notify = () => {},
}) {
  const layer = document.createElement("div");
  layer.className = "first-run-guide";
  layer.hidden = true;

  const blockers = ["top", "right", "bottom", "left"].map((side) => {
    const blocker = document.createElement("div");
    blocker.className = `first-run-guide__blocker first-run-guide__blocker--${side}`;
    blocker.setAttribute("aria-hidden", "true");
    layer.append(blocker);
    return blocker;
  });
  const spotlight = document.createElement("div");
  spotlight.className = "first-run-guide__spotlight";
  spotlight.setAttribute("aria-hidden", "true");
  layer.append(spotlight);

  const callout = document.createElement("section");
  callout.className = "first-run-guide__callout";
  callout.setAttribute("role", "dialog");
  callout.setAttribute("aria-modal", "false");
  callout.setAttribute("aria-labelledby", "firstRunGuideTitle");
  callout.setAttribute("aria-describedby", "firstRunGuideDescription");

  const calloutHead = document.createElement("header");
  calloutHead.className = "first-run-guide__head";
  const progress = document.createElement("span");
  progress.className = "first-run-guide__progress";
  const skipButton = createButton(document, "跳过", "first-run-guide__skip");
  calloutHead.append(progress, skipButton);

  const title = document.createElement("h2");
  title.id = "firstRunGuideTitle";
  const description = document.createElement("p");
  description.id = "firstRunGuideDescription";
  const featureList = document.createElement("ul");
  featureList.className = "first-run-guide__features";
  const unavailable = document.createElement("p");
  unavailable.className = "first-run-guide__unavailable";
  unavailable.textContent = "当前没有这个功能，可以继续。";
  unavailable.hidden = true;

  const actions = document.createElement("div");
  actions.className = "first-run-guide__actions";
  const backButton = createButton(document, "上一步", "secondary-button");
  const nextButton = createButton(document, "下一步", "primary-button");
  actions.append(backButton, nextButton);
  callout.append(calloutHead, title, description, featureList, unavailable, actions);
  layer.append(callout);
  document.body.append(layer);

  let active = false;
  let persistCompletion = true;
  let index = 0;
  let target = null;
  let targetDescription = null;
  let previousFocus = null;
  let renderRevision = 0;
  let geometryFrame = 0;

  function clearTargetDescription() {
    if (!target) return;
    if (targetDescription === null) target.removeAttribute("aria-describedby");
    else target.setAttribute("aria-describedby", targetDescription);
    target = null;
    targetDescription = null;
  }

  function updateGeometry() {
    if (!active) return;
    const viewport = { width: window.innerWidth, height: window.innerHeight };
    const geometry = target
      ? spotlightGeometry(target.getBoundingClientRect(), viewport)
      : null;
    layer.classList.toggle("first-run-guide--no-target", !geometry);
    if (!geometry) {
      spotlight.hidden = true;
      setRect(blockers[0], { left: 0, top: 0, width: viewport.width, height: viewport.height });
      blockers.slice(1).forEach((blocker) => setRect(blocker, { left: 0, top: 0, width: 0, height: 0 }));
    } else {
      spotlight.hidden = false;
      spotlight.style.borderRadius = window.getComputedStyle?.(target)?.borderRadius || "0";
      setRect(spotlight, geometry);
      setRect(blockers[0], { left: 0, top: 0, width: viewport.width, height: geometry.top });
      setRect(blockers[1], {
        left: geometry.left + geometry.width,
        top: geometry.top,
        width: viewport.width - geometry.left - geometry.width,
        height: geometry.height,
      });
      setRect(blockers[2], {
        left: 0,
        top: geometry.top + geometry.height,
        width: viewport.width,
        height: viewport.height - geometry.top - geometry.height,
      });
      setRect(blockers[3], {
        left: 0,
        top: geometry.top,
        width: geometry.left,
        height: geometry.height,
      });
    }
    callout.style.maxHeight = "";
    const bounds = callout.getBoundingClientRect();
    const position = calloutPosition(geometry, viewport, bounds);
    callout.style.left = `${position.left}px`;
    callout.style.top = `${position.top}px`;
    callout.style.maxHeight = `${position.maxHeight}px`;
  }

  function stopGeometryTracking() {
    if (!geometryFrame) return;
    window.cancelAnimationFrame(geometryFrame);
    geometryFrame = 0;
  }

  function startGeometryTracking() {
    stopGeometryTracking();
    const track = () => {
      if (!active) {
        geometryFrame = 0;
        return;
      }
      updateGeometry();
      geometryFrame = window.requestAnimationFrame(track);
    };
    geometryFrame = window.requestAnimationFrame(track);
  }

  function renderFeatures(step) {
    featureList.replaceChildren();
    featureList.hidden = step.id !== "models";
    if (step.id !== "models") return;
    for (const feature of modelSlotFeatures(document.getElementById("modelSlots"))) {
      const item = document.createElement("li");
      const label = document.createElement("strong");
      const copy = document.createElement("span");
      label.textContent = feature.label;
      copy.textContent = feature.description;
      item.append(label, copy);
      featureList.append(item);
    }
  }

  async function renderStep() {
    const revision = ++renderRevision;
    clearTargetDescription();
    const step = FIRST_RUN_GUIDE_STEPS[index];
    showPage(step.page);
    await new Promise((resolve) => window.requestAnimationFrame(() => window.requestAnimationFrame(resolve)));
    if (!active || revision !== renderRevision) return;

    target = document.querySelector(step.selector);
    if (target) {
      target.scrollIntoView?.({ block: "center", inline: "nearest" });
      targetDescription = target.getAttribute("aria-describedby");
      target.setAttribute("aria-describedby", "firstRunGuideDescription");
    }
    progress.textContent = `${index + 1} / ${FIRST_RUN_GUIDE_STEPS.length}`;
    title.textContent = step.title;
    description.textContent = step.description;
    unavailable.hidden = Boolean(target);
    backButton.disabled = index === 0;
    nextButton.textContent = index === FIRST_RUN_GUIDE_STEPS.length - 1 ? "完成" : "下一步";
    renderFeatures(step);
    updateGeometry();
    nextButton.focus({ preventScroll: true });
  }

  async function finish() {
    if (!active) return;
    skipButton.disabled = true;
    nextButton.disabled = true;
    backButton.disabled = true;
    try {
      if (persistCompletion) await invoke("first_run_guide_complete");
      active = false;
      stopGeometryTracking();
      renderRevision += 1;
      clearTargetDescription();
      layer.hidden = true;
      document.body.classList.remove("is-first-run-guide-active");
      if (firstRunGuideRequested(window.location.search)) {
        window.history.replaceState({}, "", window.location.pathname);
      }
      notify("引导结束。配置好后再保存。", "success");
      previousFocus?.focus?.({ preventScroll: true });
    } catch (error) {
      notify(`无法保存引导状态：${String(error)}`, "error");
    } finally {
      skipButton.disabled = false;
      nextButton.disabled = false;
      backButton.disabled = index === 0;
    }
  }

  backButton.addEventListener("click", () => {
    index = nextGuideIndex(index, -1);
    void renderStep();
  });
  nextButton.addEventListener("click", () => {
    if (index === FIRST_RUN_GUIDE_STEPS.length - 1) void finish();
    else {
      index = nextGuideIndex(index, 1);
      void renderStep();
    }
  });
  skipButton.addEventListener("click", () => { void finish(); });
  window.addEventListener("resize", updateGeometry);
  document.querySelector(".page-scroll")?.addEventListener("scroll", updateGeometry, { passive: true });

  return Object.freeze({
    start({ persist = true } = {}) {
      previousFocus = document.activeElement;
      persistCompletion = persist;
      index = 0;
      active = true;
      layer.hidden = false;
      document.body.classList.add("is-first-run-guide-active");
      startGeometryTracking();
      void renderStep();
    },
    isActive() { return active; },
    dispose() {
      active = false;
      stopGeometryTracking();
      renderRevision += 1;
      clearTargetDescription();
      window.removeEventListener("resize", updateGeometry);
      layer.remove();
    },
  });
}
