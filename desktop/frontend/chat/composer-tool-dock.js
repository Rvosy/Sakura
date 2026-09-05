import { createIcon } from "../core/icons.js";
const TOOL_ID = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}:[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/;
const SEGMENT = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/;
const ICONS = new Set([
  "camera", "folder", "globe", "link", "note", "settings", "sparkles", "terminal",
]);
const MAX_VISIBLE_TOOLS = 4;

function exactKeys(value, keys) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}

export function validateComposerToolsSnapshot(value) {
  if (!exactKeys(value, ["schemaVersion", "coreGenerationId", "tools"])
      || value.schemaVersion !== 1
      || typeof value.coreGenerationId !== "string"
      || !value.coreGenerationId
      || !Array.isArray(value.tools)
      || value.tools.length > 64) {
    throw new Error("COMPOSER_TOOLS_RESPONSE_INVALID");
  }
  const ids = new Set();
  const tools = value.tools.map((tool) => {
    if (!exactKeys(tool, ["id", "pluginId", "toolId", "label", "description", "icon", "order"])
        || typeof tool.id !== "string"
        || !TOOL_ID.test(tool.id)
        || typeof tool.pluginId !== "string"
        || !SEGMENT.test(tool.pluginId)
        || typeof tool.toolId !== "string"
        || !SEGMENT.test(tool.toolId)
        || tool.id !== `${tool.pluginId}:${tool.toolId}`
        || typeof tool.label !== "string"
        || !tool.label
        || tool.label.length > 40
        || typeof tool.description !== "string"
        || tool.description.length > 120
        || !ICONS.has(tool.icon)
        || typeof tool.order !== "number"
        || !Number.isFinite(tool.order)
        || ids.has(tool.id)) {
      throw new Error("COMPOSER_TOOLS_RESPONSE_INVALID");
    }
    ids.add(tool.id);
    return Object.freeze({ ...tool });
  });
  return Object.freeze({
    schemaVersion: 1,
    coreGenerationId: value.coreGenerationId,
    tools: Object.freeze(tools),
  });
}

function toolButton(document, tool, activate) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "composer-tool-dock__item";
  button.dataset.pluginTool = "true";
  button.dataset.toolId = tool.id;
  button.setAttribute("role", "menuitem");
  button.tabIndex = -1;
  button.title = tool.description || tool.label;

  const icon = document.createElement("span");
  icon.className = "composer-tool-dock__icon";
  icon.dataset.toolIcon = tool.icon;
  icon.setAttribute("aria-hidden", "true");
  icon.append(createIcon(document, tool.icon === "note" ? "sticky-note" : tool.icon));

  const copy = document.createElement("span");
  copy.className = "composer-tool-dock__copy";
  const label = document.createElement("strong");
  label.textContent = tool.label;
  const description = document.createElement("small");
  description.textContent = tool.description || "插件扩展";
  copy.append(label, description);
  button.append(icon, copy);
  button.addEventListener("click", () => { void activate(tool, button); });
  return button;
}

export function createComposerToolRegistry({
  list,
  invoke,
  beforeActivate = async () => {},
  afterActivate = async () => {},
  onError = () => {},
} = {}) {
  if (!list || typeof invoke !== "function") {
    throw new Error("composer tool registry requires complete dependencies");
  }
  const document = list.ownerDocument;
  let disposed = false;
  let revision = 0;

  function syncScrollableState() {
    const toolCount = list.querySelectorAll?.(".composer-tool-dock__item").length || 0;
    list.dataset.scrollable = toolCount > MAX_VISIBLE_TOOLS ? "true" : "false";
  }

  function clearPluginTools() {
    for (const item of list.querySelectorAll?.("[data-plugin-tool='true']") || []) item.remove();
    syncScrollableState();
  }

  async function activate(tool, button) {
    if (disposed || button.disabled) return false;
    button.disabled = true;
    try {
      await beforeActivate();
      await invoke("composer_tool_invoke", { toolId: tool.id });
      await afterActivate(tool);
      return true;
    } catch {
      onError(`“${tool.label}”暂时无法运行，请重试。`);
      return false;
    } finally {
      button.disabled = false;
    }
  }

  async function refresh() {
    const current = ++revision;
    try {
      const snapshot = validateComposerToolsSnapshot(await invoke("composer_tools_get"));
      if (disposed || current !== revision) return false;
      clearPluginTools();
      for (const tool of snapshot.tools) list.append(toolButton(document, tool, activate));
      syncScrollableState();
      return true;
    } catch {
      if (!disposed && current === revision) clearPluginTools();
      return false;
    }
  }

  return Object.freeze({
    refresh,
    invalidate() {
      revision += 1;
      clearPluginTools();
    },
    dispose() {
      disposed = true;
      revision += 1;
      clearPluginTools();
    },
  });
}
