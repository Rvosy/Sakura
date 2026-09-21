import { compareVersions } from "./plugin-marketplace-source.js";
// UI-facing adapter contract; this is not a registry wire format or version resolver.
export function recommended(plugin) {
  return plugin?.versions.find(version => version.number === plugin.recommendedVersion
    && !version.yanked && !version.prerelease && version.compatible !== false);
}

export function hasUpdate(plugin) {
  const version = recommended(plugin);
  return Boolean(plugin?.installed && version && compareVersions(version.number, plugin.installed) > 0);
}

export function canInstall(plugin, source) {
  const version = recommended(plugin);
  return Boolean(source?.install && version && (!plugin.installed
    || (source.canUpdate && !plugin.updateBlocked && hasUpdate(plugin))));
}

export function createCatalogLoader(source, onChange) {
  let request = null, disposed = false;
  return {
    async load() {
      request?.abort();
      const current = new AbortController(); request = current;
      if (disposed) return;
      if (!source) { onChange({ state: "unconfigured", plugins: [] }); return; }
      onChange({ state: "loading" });
      try {
        const result = await source.load({ signal: current.signal, onProgress(sourceName) {
          if (!disposed && request === current && !current.signal.aborted) onChange({ state: "loading", sourceName });
        } });
        if (disposed || request !== current || current.signal.aborted) return;
        onChange({ ...result, plugins: structuredClone(result.plugins) });
      } catch (error) {
        if (!disposed && request === current && !current.signal.aborted) onChange({ state: "error", error: String(error.message || error) });
      }
    },
    dispose() { disposed = true; request?.abort(); },
  };
}
