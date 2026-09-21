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
  let request = null, disposed = false, lastGood = null;
  return {
    async load() {
      request?.abort();
      const current = new AbortController(); request = current;
      if (disposed) return;
      if (!source) { onChange({ state: "unconfigured", plugins: [] }); return; }
      const active = () => !disposed && request === current && !current.signal.aborted;
      const accept = result => {
        if (!active()) return;
        lastGood = { ...result, plugins: structuredClone(result.plugins) };
        onChange(lastGood);
      };
      if (lastGood) onChange({ ...lastGood, refreshing: true, error: "" });
      else onChange({ state: source.cacheFirst ? "restoring" : "loading" });
      try {
        const result = await source.load({ signal: current.signal, onCached(result) {
          accept({ ...result, state: "cached", refreshing: true });
        }, onProgress(sourceName) {
          if (active() && !lastGood) onChange({ state: "loading", sourceName });
        } });
        accept({ ...result, refreshing: false });
      } catch (error) {
        if (active()) onChange({ ...(lastGood || {}), state: lastGood ? "cached" : "error",
          refreshing: false, error: String(error.message || error) });
      }
    },
    dispose() { disposed = true; request?.abort(); },
  };
}
