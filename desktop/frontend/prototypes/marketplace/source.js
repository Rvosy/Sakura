import { createPlugins, recommended } from "./data.js";

// Preview only. The production settings entry never imports this adapter.
export function createDemoMarketSource(review) {
  const failedOnce = new Set();
  let lastScenario = null;
  return {
    canUpdate: true,
    reset() { lastScenario = null; },
    async load() {
      if (["cached", "unavailable"].includes(review.scenario) && lastScenario === review.scenario) review.reconnect();
      lastScenario = review.scenario;
      if (review.scenario === "unavailable") throw new Error("DEMO_OFFLINE");
      const plugins = createPlugins().map(p => {
        const local = review.state.plugins.find(item => item.pluginId === p.id);
        if (local) Object.assign(p, { name: local.name, author: local.author, description: local.description,
          kind: {extension: "功能扩展", provider: "功能引擎", infrastructure: "系统组件"}[local.presentation.kind] });
        const next = recommended(p);
        return { ...p, recommendedVersion: next?.number,
          compatibilityReason: !next ? "需要 Plugin API 5" : next !== p.versions[0] ? `推荐兼容版本 ${next.number}` : "",
          versions: p.versions.map(v => ({ ...v, compatible: v.api === 4 })) };
      });
      return { state: review.scenario === "cached" ? "cached" : "ready", plugins,
        updatedAt: review.scenario === "cached" ? "2026-09-18 14:30" : "刚刚" };
    },
    async install(plugin, { signal, onProgress }) {
      await new Promise((resolve, reject) => {
        let progress = 0;
        const cleanup = () => { clearInterval(timer); signal.removeEventListener("abort", abort); };
        const abort = () => { cleanup(); reject(new DOMException("Aborted", "AbortError")); };
        const timer = setInterval(() => {
          progress += 10;
          if (["cached", "unavailable"].includes(review.scenario)
            || (progress === 40 && review.scenario === "download-error" && !failedOnce.has(plugin.id))) {
            failedOnce.add(plugin.id); cleanup(); reject(new Error("DEMO_DOWNLOAD_FAILED")); return;
          }
          onProgress(progress, progress < 80 ? "downloading" : "installing");
          if (progress === 100) { cleanup(); resolve(); }
        }, 240);
        signal.addEventListener("abort", abort, { once: true });
        if (signal.aborted) abort();
      });
      if (signal.aborted) throw new DOMException("Aborted", "AbortError");
      let local = review.state.plugins.find(item => item.pluginId === plugin.id);
      if (!local) {
        local = { installId: "pi_user_" + crypto.randomUUID(), pluginId: plugin.id, name: plugin.name,
          author: plugin.author, description: plugin.description, enabled: false, required: false,
          supported: true, source: "user", canUninstall: true, provides: [], requires: [], missingServices: [],
          state: "disabled", reasonCode: "PLUGIN_DISABLED", sections: [],
          presentation: { kind: "extension", category: {工具: "tools", 连接: "connectivity", 表现: "visual"}[plugin.category] || "other", icon: plugin.icon } };
        review.state.plugins.push(local);
      }
      local.version = plugin.recommendedVersion;
      review.state.revision++;
    },
  };
}
