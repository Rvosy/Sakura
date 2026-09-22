import { migrationStatus } from "../lifecycle.js";

export function createMigrationStatus({ document, window, invoke, onError }) {
  const banner = document.createElement("div");
  banner.className = "migration-status";
  banner.hidden = true;
  const message = document.createElement("span");
  message.setAttribute("role", "status");
  const restart = document.createElement("button");
  restart.type = "button";
  restart.className = "secondary-button";
  restart.textContent = "重启核心";
  banner.append(message, restart);
  document.querySelector(".detail-card").prepend(banner);
  let disposed = false, reading = false;
  async function refresh() {
    if (disposed || reading) return;
    reading = true;
    try {
      const publication = await invoke("runtime_lifecycle_snapshot");
      if (disposed) return;
      const status = migrationStatus(publication);
      banner.hidden = !status;
      message.textContent = status?.message || "";
      restart.hidden = !(status?.state === "failed"
        || (status?.state === "completed" && publication.snapshot?.readiness === "failed"));
    } catch {
      // Preserve the last observed migration while transport reconnects.
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
  return { dispose() { disposed = true; window.clearInterval(timer); banner.remove(); } };
}
