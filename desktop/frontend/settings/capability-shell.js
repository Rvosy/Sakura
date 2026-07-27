const REQUIRED_KEYS = Object.freeze([
  "schemaVersion",
  "windowGeneration",
  "availableSections",
  "readOnlySections",
  "unavailableReasons",
]);
const SENSITIVE_KEY = /(password|api.?key|credential|secret|token)/i;

function isStringArray(value) {
  return Array.isArray(value) && value.every((item) => typeof item === "string" && item.length > 0);
}

export function validateCapabilityManifest(manifest) {
  if (!manifest || typeof manifest !== "object" || Array.isArray(manifest)) {
    throw new Error("invalid settings capability manifest");
  }
  for (const key of REQUIRED_KEYS) {
    if (!(key in manifest)) throw new Error(`missing settings capability field: ${key}`);
  }
  for (const key of Object.keys(manifest)) {
    if (SENSITIVE_KEY.test(key)) throw new Error("settings capability manifest contains a sensitive field");
  }
  if (manifest.schemaVersion !== 1) throw new Error("unsupported settings capability schema");
  if (!Number.isSafeInteger(manifest.windowGeneration) || manifest.windowGeneration < 1) {
    throw new Error("invalid settings window generation");
  }
  if (!isStringArray(manifest.availableSections) || !isStringArray(manifest.readOnlySections)) {
    throw new Error("invalid settings capability sections");
  }
  if (
    !manifest.unavailableReasons ||
    typeof manifest.unavailableReasons !== "object" ||
    Array.isArray(manifest.unavailableReasons) ||
    Object.entries(manifest.unavailableReasons).some(
      ([section, reason]) => !section || typeof reason !== "string" || !reason,
    )
  ) {
    throw new Error("invalid unavailable settings reasons");
  }
  return Object.freeze({
    schemaVersion: manifest.schemaVersion,
    windowGeneration: manifest.windowGeneration,
    availableSections: Object.freeze([...new Set(manifest.availableSections)]),
    readOnlySections: Object.freeze([...new Set(manifest.readOnlySections)]),
    unavailableReasons: Object.freeze({ ...manifest.unavailableReasons }),
  });
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
    if (unavailable) {
      item.title = manifest.unavailableReasons[section] || "该设置能力尚未迁移";
    }
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

  const firstSection = [...enabled][0];
  if (firstSection) {
    const item = document.querySelector(`.nav-item[data-page="${firstSection}"]`);
    const page = document.getElementById(`page-${firstSection}`);
    item?.classList.add("is-active");
    item?.setAttribute("aria-current", "page");
    if (page) {
      page.hidden = false;
      page.classList.add("is-active");
      if (readOnly.has(firstSection)) {
        for (const control of page.querySelectorAll("input, select, textarea, button")) {
          control.disabled = true;
        }
      }
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

  const writable = available.has("character") || available.has("appearance");
  document.getElementById("applyButton").hidden = !writable;
  document.getElementById("saveButton").hidden = !writable;
  const cancel = document.getElementById("cancelButton");
  cancel.textContent = writable ? "取消" : "关闭";
  return manifest;
}
