import { migrationStatus } from "../lifecycle.js";

export function createMigrationStatus({ document, window, invoke, onError, reveal = async () => {} }) {
  const banner = document.createElement("div");
  banner.className = "migration-status";
  banner.hidden = true;
  const message = document.createElement("span");
  message.setAttribute("role", "status");
  const progress = document.createElement("progress");
  progress.setAttribute("aria-label", "插件迁移进度");
  progress.hidden = true;
  const restart = document.createElement("button");
  restart.type = "button";
  restart.className = "secondary-button";
  restart.textContent = "重启核心";
  banner.append(message, progress, restart);
  document.querySelector(".detail-card").prepend(banner);
  const navigation = document.querySelector(".nav-card");
  let disposed = false, reading = false, loading = false, startupSettled = false;
  let settleStartup;
  const ready = new Promise(resolve => { settleStartup = resolve; });
  function settle() {
    startupSettled = true;
    settleStartup();
  }
  function finishStartup() {
    loading = false;
    delete document.body.dataset.migrationLoading;
    navigation.inert = false;
    if (banner.dataset.state === "completed") banner.hidden = true;
  }
  async function refresh() {
    if (disposed || reading) return;
    reading = true;
    try {
      const publication = await invoke("runtime_lifecycle_snapshot");
      if (disposed) return;
      const status = migrationStatus(publication);
      let waiting = false;
      if (!startupSettled && status) {
        waiting = status.state === "running"
          || !["ready", "degraded", "setup_required", "failed"].includes(publication.snapshot?.readiness);
        if (!waiting && publication.snapshot?.readiness !== "failed") {
          // Core can publish chat/visual readiness before optional plugins finish.
          // Read their existing startup state before requesting voice settings.
          const plugins = await invoke("settings_plugins_get");
          if (disposed) return;
          waiting = ["starting", "waiting"].includes(plugins.state);
        }
      }
      if (waiting && !loading) {
        loading = true;
        document.body.dataset.migrationLoading = "true";
        navigation.inert = true;
      }
      banner.dataset.state = status?.state || "";
      banner.hidden = !status || (status.state === "completed" && !loading
        && publication.snapshot?.readiness !== "failed");
      message.textContent = status?.message || "";
      if (loading && status?.state === "completed" && publication.snapshot?.readiness !== "failed") {
        message.textContent = "正在启动 Sakura";
      }
      progress.hidden = status?.state !== "running";
      if (!progress.hidden) {
        progress.max = status.total;
        progress.value = status.completed;
      }
      restart.hidden = !(status?.state === "failed"
        || (status?.state === "completed" && publication.snapshot?.readiness === "failed"));
      if (!startupSettled) {
        if (loading) await reveal();
        if (!waiting) settle();
      }
    } catch {
      // Preserve the last observed migration while transport reconnects.
      if (!loading) settle();
    } finally { reading = false; }
  }
  restart.addEventListener("click", async () => {
    restart.disabled = true;
    try { await invoke("settings_restart_after_migration"); }
    catch (error) { onError(String(error)); }
    finally { if (!disposed) restart.disabled = false; }
  });
  const timer = window.setInterval(refresh, 500);
  void refresh();
  return {
    ready,
    finishStartup,
    dispose() {
      disposed = true;
      window.clearInterval(timer);
      settle();
      finishStartup();
      banner.remove();
    },
  };
}
