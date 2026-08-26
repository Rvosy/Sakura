const V1_KEYS = Object.freeze([
  "schemaVersion",
  "windowGeneration",
  "availableSections",
  "readOnlySections",
  "unavailableReasons",
]);
const V2_KEYS = Object.freeze([
  "schemaVersion",
  "windowGeneration",
  "sections",
  "unavailableReasons",
]);
const SENSITIVE_KEY = /(password|api.?key|credential$|secret|(^|_)token($|_))/i;
const STATUSES = new Set(["available", "read_only", "unavailable"]);

function isStringArray(value) {
  return Array.isArray(value) && value.every((item) => typeof item === "string" && item.length > 0);
}

function assertNoSensitiveKeys(value) {
  if (!value || typeof value !== "object") return;
  for (const [key, child] of Object.entries(value)) {
    if (SENSITIVE_KEY.test(key)) throw new Error("settings capability manifest contains a sensitive field");
    assertNoSensitiveKeys(child);
  }
}

function reasons(value) {
  if (
    !value
    || typeof value !== "object"
    || Array.isArray(value)
    || Object.entries(value).some(([key, reason]) => !key || typeof reason !== "string" || !reason)
  ) throw new Error("invalid unavailable settings reasons");
  return Object.freeze({ ...value });
}

function normalizeV1(manifest) {
  if (!isStringArray(manifest.availableSections) || !isStringArray(manifest.readOnlySections)) {
    throw new Error("invalid settings capability sections");
  }
  const sections = {};
  for (const section of new Set(manifest.availableSections)) {
    sections[section] = Object.freeze({ status: "available", features: Object.freeze({}) });
  }
  for (const section of new Set(manifest.readOnlySections)) {
    if (!sections[section]) sections[section] = Object.freeze({ status: "read_only", features: Object.freeze({}) });
  }
  return Object.freeze({
    schemaVersion: 1,
    windowGeneration: manifest.windowGeneration,
    sections: Object.freeze(sections),
    availableSections: Object.freeze([...new Set(manifest.availableSections)]),
    readOnlySections: Object.freeze([...new Set(manifest.readOnlySections)]),
    unavailableReasons: reasons(manifest.unavailableReasons),
  });
}

function normalizeV2(manifest) {
  if (!manifest.sections || typeof manifest.sections !== "object" || Array.isArray(manifest.sections)) {
    throw new Error("invalid settings capability sections");
  }
  const sections = {};
  for (const [section, value] of Object.entries(manifest.sections)) {
    if (!section || !value || typeof value !== "object" || Array.isArray(value)) continue;
    const status = STATUSES.has(value.status) ? value.status : "unavailable";
    const rawFeatures = value.features;
    if (!rawFeatures || typeof rawFeatures !== "object" || Array.isArray(rawFeatures)) {
      throw new Error("invalid settings capability features");
    }
    const features = {};
    for (const [feature, featureStatus] of Object.entries(rawFeatures)) {
      if (!feature) continue;
      features[feature] = STATUSES.has(featureStatus) ? featureStatus : "unavailable";
    }
    sections[section] = Object.freeze({ status, features: Object.freeze(features) });
  }
  const availableSections = Object.keys(sections).filter((key) => sections[key].status === "available");
  const readOnlySections = Object.keys(sections).filter((key) => sections[key].status === "read_only");
  return Object.freeze({
    schemaVersion: 2,
    windowGeneration: manifest.windowGeneration,
    sections: Object.freeze(sections),
    availableSections: Object.freeze(availableSections),
    readOnlySections: Object.freeze(readOnlySections),
    unavailableReasons: reasons(manifest.unavailableReasons),
  });
}

export function validateCapabilityManifest(manifest) {
  if (!manifest || typeof manifest !== "object" || Array.isArray(manifest)) {
    throw new Error("invalid settings capability manifest");
  }
  const required = manifest.schemaVersion === 1 ? V1_KEYS : manifest.schemaVersion === 2 ? V2_KEYS : null;
  if (!required) throw new Error("unsupported settings capability schema");
  for (const key of required) {
    if (!(key in manifest)) throw new Error(`missing settings capability field: ${key}`);
  }
  assertNoSensitiveKeys(manifest);
  if (!Number.isSafeInteger(manifest.windowGeneration) || manifest.windowGeneration < 1) {
    throw new Error("invalid settings window generation");
  }
  return manifest.schemaVersion === 1 ? normalizeV1(manifest) : normalizeV2(manifest);
}

export function featureStatus(manifest, feature) {
  for (const section of Object.values(manifest.sections || {})) {
    if (feature in section.features) return section.features[feature];
  }
  return "unavailable";
}

export function inputVisualEffectModes(manifest) {
  const modes = [
    { id: "solid", label: "纯色块", feature: null },
    {
      id: "gaussian_blur",
      label: "高斯模糊",
      feature: "appearance.input_visual_effect.gaussian_blur",
    },
    {
      id: "liquid_glass",
      label: "液态玻璃",
      feature: "appearance.input_visual_effect.liquid_glass",
    },
  ];
  return Object.freeze(modes.map(({ id, label, feature }) => {
    const disabled = feature ? featureStatus(manifest, feature) !== "available" : false;
    return Object.freeze({
      id,
      label,
      disabled,
      reason: disabled
        ? manifest.unavailableReasons?.[feature] || "当前系统不支持此效果"
        : "",
    });
  }));
}

export function applyCapabilityManifest(document, input) {
  const manifest = validateCapabilityManifest(input);
  const available = new Set(manifest.availableSections);
  const readOnly = new Set(manifest.readOnlySections);
  const enabled = new Set([...available, ...readOnly]);
  document.body.dataset.settingsHost = "runtime-v2";
  document.body.dataset.windowGeneration = String(manifest.windowGeneration);

  for (const item of document.querySelectorAll(".nav-item[data-page]")) {
    const section = item.dataset.page;
    const unavailable = !enabled.has(section);
    item.disabled = unavailable;
    item.classList.remove("is-active");
    item.removeAttribute("aria-current");
    if (unavailable) item.title = manifest.unavailableReasons[section] || "该设置能力尚未迁移";
  }
  for (const page of document.querySelectorAll(".settings-page")) {
    page.classList.remove("is-active");
    page.hidden = true;
  }

  const shell = document.getElementById("capabilityShell");
  shell.hidden = enabled.size > 0;
  if (enabled.size === 0) {
    document.getElementById("pageTitle").textContent = "设置";
    document.getElementById("pageSubtitle").textContent = "Runtime v2 设置窗口已就绪";
  }

  const firstSection = Array.from(document.querySelectorAll(".nav-item[data-page]"))
    .map((item) => item.dataset.page)
    .find((section) => enabled.has(section));
  if (firstSection) {
    const item = document.querySelector(`.nav-item[data-page="${firstSection}"]`);
    const page = document.getElementById(`page-${firstSection}`);
    item?.classList.add("is-active");
    item?.setAttribute("aria-current", "page");
    if (page) {
      page.hidden = false;
      page.classList.add("is-active");
      if (readOnly.has(firstSection)) {
        for (const control of page.querySelectorAll("input, select, textarea, button")) control.disabled = true;
      }
    }
  }

  for (const control of document.querySelectorAll("[data-settings-feature]")) {
    const status = featureStatus(manifest, control.dataset.settingsFeature);
    if (status !== "available") control.disabled = true;
    if (status === "unavailable") {
      control.title = manifest.unavailableReasons[control.dataset.settingsFeature] || "该设置能力尚未迁移";
    }
  }

  for (const item of document.querySelectorAll(".nav-item[data-page]")) {
    if (!enabled.has(item.dataset.page)) continue;
    item.addEventListener("click", () => {
      for (const page of document.querySelectorAll(".settings-page")) {
        page.hidden = page.id !== `page-${item.dataset.page}`;
      }
    });
  }

  const writable = [...available].some((section) => manifest.sections[section]?.status === "available");
  document.getElementById("applyButton").hidden = !writable;
  document.getElementById("saveButton").hidden = !writable;
  const cancel = document.getElementById("cancelButton");
  cancel.textContent = writable ? "取消" : "关闭";
  return manifest;
}
