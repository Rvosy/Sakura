const invoke = window.__TAURI__?.core?.invoke;
const list = document.querySelector("#history-list");
const status = document.querySelector("#status");
const loadMore = document.querySelector("#load-more");
const refresh = document.querySelector("#refresh");

let cursor = null;
let loading = false;

function applyTheme(theme = {}) {
  const root = document.documentElement.style;
  root.setProperty("--primary", theme.primary_color || "#d55b91");
  root.setProperty("--page", theme.page_background_color || "#f7f4f8");
  root.setProperty("--panel", theme.panel_background_color || "#f3e2ea");
  root.setProperty("--text", theme.text_color || "#29242d");
  root.setProperty("--muted", theme.muted_text_color || "#736a76");
  root.setProperty("--border", theme.border_color || "#d7bcc9");
}

function messageElement(item) {
  const article = document.createElement("article");
  article.className = `message message-${item.role === "assistant" ? "assistant" : "user"}`;
  const meta = document.createElement("div");
  meta.className = "message-meta";
  meta.textContent = `${item.role === "assistant" ? "Sakura" : "你"} · ${item.createdAt || ""}`;
  const content = document.createElement("p");
  content.textContent = item.translation || item.content || "";
  article.append(meta, content);
  return article;
}

function applyPage(page, { prepend = false } = {}) {
  if (page?.theme) applyTheme(page.theme);
  const fragment = document.createDocumentFragment();
  for (const item of page?.items || []) fragment.append(messageElement(item));
  if (prepend) list.prepend(fragment);
  else list.append(fragment);
  cursor = page?.nextCursor ?? null;
  loadMore.hidden = !page?.hasMore;
  status.textContent = list.childElementCount ? `已加载 ${list.childElementCount} 条记录` : "暂无对话记录。";
}

async function loadInitial() {
  if (!invoke || loading) return;
  loading = true;
  refresh.disabled = true;
  loadMore.disabled = true;
  try {
    const page = await invoke("load_request");
    list.textContent = "";
    applyPage(page);
  } catch (error) {
    status.textContent = `历史记录读取失败：${error}`;
  } finally {
    loading = false;
    refresh.disabled = false;
    loadMore.disabled = false;
  }
}

async function loadEarlier() {
  if (!invoke || loading || cursor == null) return;
  loading = true;
  loadMore.disabled = true;
  try {
    const page = await invoke("host_call", {
      method: "history.page",
      params: { cursor, limit: 50 },
    });
    applyPage(page, { prepend: true });
  } catch (error) {
    status.textContent = `更早记录读取失败：${error}`;
  } finally {
    loading = false;
    loadMore.disabled = false;
  }
}

refresh.addEventListener("click", loadInitial);
loadMore.addEventListener("click", loadEarlier);
loadInitial();
