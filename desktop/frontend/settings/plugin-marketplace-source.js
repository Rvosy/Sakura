// Convert the published catalog to the market's display model.
const versionParts = value => /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([\w.-]+))?(?:\+[\w.-]+)?$/.exec(value);
export function compareVersions(a, b) {
  const left = versionParts(a), right = versionParts(b);
  if (!left || !right) throw new Error("版本号格式无效。");
  for (let i = 1; i <= 3; i++) { if (BigInt(left[i]) !== BigInt(right[i])) return BigInt(left[i]) > BigInt(right[i]) ? 1 : -1; }
  if (!left[4] || !right[4]) return left[4] ? -1 : right[4] ? 1 : 0;
  const x = left[4].split("."), y = right[4].split(".");
  for (let i = 0; i < Math.max(x.length, y.length); i++) {
    if (x[i] === y[i]) continue;
    if (x[i] === undefined) return -1;
    if (y[i] === undefined) return 1;
    const xn = /^\d+$/.test(x[i]), yn = /^\d+$/.test(y[i]);
    if (xn && yn) return BigInt(x[i]) > BigInt(y[i]) ? 1 : -1;
    if (xn !== yn) return xn ? -1 : 1;
    return x[i] > y[i] ? 1 : -1;
  }
  return 0;
}

export function catalogPlugins(catalog, context) {
  if (catalog.schema_version !== 1 || !Array.isArray(catalog.plugins)) throw new Error("插件目录格式无效。");
  const services = new Set(context.services);
  return catalog.plugins.map(plugin => {
    const versions = plugin.versions.map(release => {
      const manifest = release.manifest;
      const missing = (manifest?.requires || []).filter(key => !services.has(key));
      const compatible = manifest?.api === context.api && !missing.length;
      return { number: release.version, api: manifest?.api, yanked: release.yanked ? release.yank_reason : "",
        prerelease: release.prerelease, compatible, package: release.package,
        manifest, date: release.date || "", notes: release.notes || "",
        reason: !manifest ? "该版本已撤回" : manifest.api !== context.api ? "插件 API 版本不匹配" : missing.length ? `需要先启用：${missing.join("、")}` : "" };
    }).sort((a, b) => compareVersions(b.number, a.number));
    const next = versions.find(v => !v.yanked && !v.prerelease && v.compatible && v.package);
    const display = next || versions.find(v => v.manifest);
    const manifest = display?.manifest || {};
    const presentation = manifest.presentation || {};
    return { id: plugin.id, name: manifest.name || plugin.id, author: manifest.author || "",
      description: manifest.description || "", body: manifest.description || "", repository: plugin.repository,
      category: ({ tool: "工具", voice: "语音", memory: "记忆", connection: "连接", model: "表现", visual: "表现" })[presentation.category] || "工具",
      kind: ({ provider: "服务", tool: "工具", extension: "扩展" })[presentation.kind] || "插件",
      icon: presentation.icon || "puzzle", versions, recommendedVersion: next?.number,
      compatibilityReason: next ? "" : display?.reason || "暂无可安装版本" };
  });
}

export function createMarketplaceSource({ invoke, Channel, host, randomUUID = () => crypto.randomUUID() }) {
  return {
    canUpdate: true,
    async load({ signal, onProgress }) {
      const progress = new Channel();
      progress.onmessage = message => { if (!signal.aborted) onProgress?.(message.source); };
      const { catalog, context } = await invoke("settings_marketplace_catalog", { progress });
      signal.throwIfAborted();
      return { state: "ready", plugins: catalogPlugins(catalog, context) };
    },
    async install(plugin, { signal, onProgress }) {
      if (host.isDirty()) throw new Error("请先保存或还原设置修改。");
      const version = plugin.versions.find(v => v.number === plugin.recommendedVersion);
      if (!version || version.yanked || !version.compatible) throw new Error("该版本不可安装。");
      const current = await invoke("settings_plugins_get");
      const existing = current.plugins.find(p => p.pluginId === plugin.id);
      if (existing?.source === "bundled") throw new Error("内置插件随应用更新。");
      if (existing?.enabled) throw new Error("请先停用插件再更新。");
      if (existing && compareVersions(existing.version, version.number) >= 0) throw new Error("已安装当前版本或更新版本。");
      signal.throwIfAborted();
      const requestId = randomUUID(), progress = new Channel();
      const cancel = () => { void invoke("settings_marketplace_cancel", { requestId }).catch(() => {}); };
      progress.onmessage = message => {
        if (signal.aborted && message.phase === "downloading") cancel();
        onProgress(message.progress || 0, message.phase, message.source || "");
      };
      signal.addEventListener("abort", cancel, { once: true });
      try {
        return await invoke("settings_marketplace_install", { requestId, pluginId: plugin.id, version: version.number,
          windowGeneration: current.windowGeneration, coreGenerationId: current.coreGenerationId, revision: current.revision, progress });
      } finally { signal.removeEventListener("abort", cancel); }
    },
  };
}
