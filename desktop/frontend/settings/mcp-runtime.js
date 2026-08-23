const SNAPSHOT_KEYS = Object.freeze([
  "schemaVersion", "desktop", "desktopEnabled", "configState", "reasonCode", "servers",
  "windowGeneration", "coreGenerationId",
]);
const DESKTOP_KEYS = Object.freeze(["supported", "label", "experimentalText"]);
const SERVER_KEYS = Object.freeze([
  "serverId", "transport", "enabled", "state", "reasonCode", "toolCount",
]);
const SERVER_STATES = new Set(["disabled", "starting", "ready", "degraded", "stopping", "stopped"]);
const IDENTIFIER = /^[A-Za-z0-9_.-]{1,64}$/;
const REASON = /^[A-Z0-9_]{1,64}$/;

function exactKeys(value, keys) {
  return Boolean(
    value && typeof value === "object" && !Array.isArray(value)
    && Object.keys(value).length === keys.length
    && keys.every((key) => Object.hasOwn(value, key)),
  );
}

function validateServer(server) {
  if (!exactKeys(server, SERVER_KEYS)) throw new Error("invalid MCP server status");
  if (!IDENTIFIER.test(server.serverId)) throw new Error("invalid MCP server ID");
  if (!["stdio", "sse"].includes(server.transport)) throw new Error("invalid MCP transport");
  if (typeof server.enabled !== "boolean") throw new Error("invalid MCP server enabled state");
  if (!SERVER_STATES.has(server.state)) throw new Error("invalid MCP server state");
  if (!REASON.test(server.reasonCode)) throw new Error("invalid MCP server reason");
  if (!Number.isSafeInteger(server.toolCount) || server.toolCount < 0 || server.toolCount > 512) {
    throw new Error("invalid MCP server tool count");
  }
  return Object.freeze({ ...server });
}

export function validateMcpSnapshot(input) {
  if (!exactKeys(input, SNAPSHOT_KEYS) || input.schemaVersion !== 1) {
    throw new Error("invalid MCP settings snapshot");
  }
  if (!exactKeys(input.desktop, DESKTOP_KEYS)
      || typeof input.desktop.supported !== "boolean"
      || typeof input.desktop.label !== "string"
      || !input.desktop.label
      || input.desktop.label.length > 80
      || typeof input.desktop.experimentalText !== "string"
      || input.desktop.experimentalText.length > 240) {
    throw new Error("invalid desktop MCP status");
  }
  if (typeof input.desktopEnabled !== "boolean") throw new Error("invalid desktop MCP preference");
  if (!["valid", "missing", "invalid"].includes(input.configState) || !REASON.test(input.reasonCode)) {
    throw new Error("invalid MCP configuration status");
  }
  if (!Array.isArray(input.servers) || input.servers.length > 16) {
    throw new Error("invalid MCP server list");
  }
  if (!Number.isSafeInteger(input.windowGeneration) || input.windowGeneration < 1) {
    throw new Error("invalid MCP settings window generation");
  }
  if (typeof input.coreGenerationId !== "string" || !input.coreGenerationId) {
    throw new Error("invalid MCP Core generation");
  }
  return Object.freeze({
    ...input,
    desktop: Object.freeze({ ...input.desktop }),
    servers: Object.freeze(input.servers.map(validateServer)),
  });
}

function transitionError(error) {
  const message = String(error?.message || error || "");
  return [
    "SETTINGS_CORE_GENERATION_MISMATCH", "SETTINGS_CORE_UNAVAILABLE", "CORE_RESTART", "CORE_GENERATION",
  ].some((code) => message.includes(code));
}

function stateCopy(snapshot) {
  if (snapshot.configState === "invalid") return "MCP 配置无效；其他功能仍可继续使用。";
  if (snapshot.configState === "missing") return "未找到 mcp.yaml，MCP 当前未启用。";
  const ready = snapshot.servers.filter((server) => server.state === "ready");
  const degraded = snapshot.servers.filter((server) => server.state === "degraded");
  if (ready.length) {
    const tools = ready.reduce((sum, item) => sum + item.toolCount, 0);
    return `${ready.length} 个 MCP 服务器已就绪，共 ${tools} 个工具。`;
  }
  if (degraded.length) return "MCP 服务器启动失败；聊天和其他工具不受影响。";
  if (snapshot.servers.some((server) => server.state === "starting")) return "MCP 服务器正在启动…";
  return "MCP 当前未启用。";
}

export function createMcpController({
  document,
  invoke,
  onDirty,
  wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds)),
}) {
  const toggle = document.getElementById("desktopMcp");
  const status = document.getElementById("mcpStatusStrip");
  const serverStatus = document.getElementById("mcpServerStatus");
  let snapshot = null;
  let baseline = false;
  let disposed = false;
  let rebindPromise = null;

  function render(next) {
    const group = toggle.closest?.(".settings-group");
    if (group) group.hidden = !next.desktop.supported;
    toggle.checked = next.desktopEnabled;
    toggle.disabled = !next.desktop.supported;
    const row = toggle.closest?.(".setting-row");
    if (row) {
      row.hidden = !next.desktop.supported;
      const title = row.querySelector?.(".setting-title");
      const description = row.querySelector?.(".setting-desc");
      if (title) title.textContent = `${next.desktop.label} 桌面控制`;
      if (description) {
        const experimental = next.desktop.experimentalText ? `${next.desktop.experimentalText}。` : "";
        description.textContent = `${experimental}保存后会受控重启 Core。`;
      }
    }
    if (status) {
      status.textContent = stateCopy(next);
      status.dataset.state = next.configState === "valid" ? next.reasonCode.toLowerCase() : next.configState;
    }
    if (serverStatus) {
      const rows = next.servers.map((server) => {
        const item = document.createElement("div");
        item.className = `mcp-server-state is-${server.state}`;
        item.textContent = `${server.serverId} · ${server.transport} · ${server.state} · ${server.toolCount} tools`;
        return item;
      });
      serverStatus.replaceChildren(...rows);
    }
  }

  function initialize(input, { preserveDraft = false } = {}) {
    const draft = preserveDraft && snapshot ? toggle.checked : null;
    snapshot = validateMcpSnapshot(input);
    baseline = snapshot.desktopEnabled;
    render(snapshot);
    if (draft !== null && snapshot.desktop.supported) toggle.checked = draft;
    onDirty();
  }

  async function bindCurrent(previousGeneration, { requireChange, preserveDraft }) {
    if (rebindPromise) return rebindPromise;
    const deadline = Date.now() + 10_000;
    rebindPromise = (async () => {
      let lastError = null;
      while (!disposed && Date.now() < deadline) {
        try {
          const next = validateMcpSnapshot(await invoke("settings_mcp_get"));
          if (!requireChange || next.coreGenerationId !== previousGeneration) {
            initialize(next, { preserveDraft });
            return next;
          }
        } catch (error) {
          lastError = error;
        }
        await wait(100);
      }
      throw new Error(`MCP_CORE_RESTART_NOT_READY${lastError ? `: ${String(lastError)}` : ""}`);
    })().finally(() => { rebindPromise = null; });
    return rebindPromise;
  }

  toggle.addEventListener("change", onDirty);

  return Object.freeze({
    initialize,
    isDirty() { return Boolean(snapshot && toggle.checked !== baseline); },
    async save() {
      if (!snapshot) throw new Error("MCP settings are not initialized");
      const previousGeneration = snapshot.coreGenerationId;
      try {
        const result = await invoke("settings_mcp_save", {
          windowGeneration: snapshot.windowGeneration,
          coreGenerationId: previousGeneration,
          settings: { desktopEnabled: toggle.checked },
        });
        if (result?.changePlan !== "applied") {
          throw new Error("MCP_SETTINGS_CHANGE_PLAN_INVALID");
        }
      } catch (error) {
        if (transitionError(error)) {
          await bindCurrent(previousGeneration, { requireChange: false, preserveDraft: true });
        }
        throw error;
      }
      return bindCurrent(previousGeneration, { requireChange: false, preserveDraft: false });
    },
    async refreshCurrent() {
      return bindCurrent(snapshot?.coreGenerationId || "", { requireChange: false, preserveDraft: true });
    },
    discard() {
      if (snapshot) toggle.checked = baseline;
      onDirty();
    },
    dispose() {
      disposed = true;
      snapshot = null;
      rebindPromise = null;
    },
  });
}
