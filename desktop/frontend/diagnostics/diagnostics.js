const invoke = window.__TAURI__?.core?.invoke;

document.addEventListener("contextmenu", (event) => event.preventDefault());

const cards = document.querySelector("#cards");
const status = document.querySelector("#status");
const raw = document.querySelector("#raw");
const refresh = document.querySelector("#refresh");

function applyTheme(theme = {}) {
  const root = document.documentElement.style;
  root.setProperty("--primary", theme.primary_color || "#8fb6eb");
  root.setProperty("--page", theme.page_background_color || "#11141a");
  root.setProperty("--panel", theme.panel_background_color || "#1b222d");
  root.setProperty("--text", theme.text_color || "#edf1f5");
  root.setProperty("--muted", theme.muted_text_color || "#aeb9c7");
  root.setProperty("--border", theme.border_color || "#303c4e");
}

function card(title, state, rows) {
  const article = document.createElement("article");
  article.className = "card";
  const heading = document.createElement("div");
  heading.className = "card-heading";
  const name = document.createElement("h2");
  name.textContent = title;
  const badge = document.createElement("span");
  badge.className = `badge badge-${state === true ? "ok" : state === false ? "bad" : "neutral"}`;
  badge.textContent = state === true ? "正常" : state === false ? "异常" : String(state || "信息");
  heading.append(name, badge);
  const body = document.createElement("dl");
  for (const [label, value] of rows) {
    const term = document.createElement("dt");
    term.textContent = label;
    const detail = document.createElement("dd");
    detail.textContent = String(value ?? "—");
    body.append(term, detail);
  }
  article.append(heading, body);
  return article;
}

function render(snapshot) {
  applyTheme(snapshot.theme || {});
  cards.textContent = "";
  cards.append(
    card("Brain Host", snapshot.brain?.state === "ready", [
      ["状态", snapshot.brain?.state],
      ["角色", snapshot.brain?.characterId],
      ["忙碌", snapshot.brain?.busy ? "是" : "否"],
    ]),
    card("插件", snapshot.plugins?.failed === 0, [
      ["已加载", snapshot.plugins?.loaded],
      ["失败", snapshot.plugins?.failed],
    ]),
    card("MCP", snapshot.mcp?.ready, [["工具数量", snapshot.mcp?.toolCount]]),
    card("TTS", snapshot.tts?.ready, [["服务", snapshot.tts?.service]]),
    card("资源", "资源", [
      ["活动数量", snapshot.resources?.activeCount],
      ["标签", (snapshot.resources?.labels || []).join("、")],
    ]),
    card("调度器", snapshot.scheduler?.running, [
      ["任务", (snapshot.scheduler?.jobs || []).join("、")],
    ]),
  );
  raw.textContent = JSON.stringify(snapshot, null, 2);
  status.textContent = `最近刷新：${new Date().toLocaleTimeString()}`;
}

async function load({ initial = false } = {}) {
  if (!invoke) return;
  refresh.disabled = true;
  try {
    const snapshot = initial
      ? await invoke("load_request")
      : await invoke("host_call", { method: "diagnostics.snapshot", params: {} });
    render(snapshot);
  } catch (error) {
    status.textContent = `诊断状态读取失败：${error}`;
  } finally {
    refresh.disabled = false;
  }
}

refresh.addEventListener("click", () => load());
load({ initial: true });
