import { catalogPlugins } from "../settings/plugin-marketplace-source.js";
import { syncInstalledPlugins, updateCandidates } from "../settings/plugin-marketplace-runtime.js";

export async function checkUpdates(invoke, onError) {
  const [application, plugins] = await Promise.allSettled([
    invoke("startup_update_check"), invoke("startup_plugin_catalog"),
  ]);
  for (const result of [application, plugins]) {
    if (result.status === "rejected") onError(result.reason);
  }
  if (plugins.status === "fulfilled" && plugins.value) {
    try {
      const { catalog, context, installed } = plugins.value;
      const candidates = updateCandidates(syncInstalledPlugins(catalogPlugins(catalog, context), installed));
      return await invoke("plugin_update_candidates", { plugins: candidates });
    } catch (error) {
      onError(error);
    }
  }
  return application.status === "fulfilled" ? application.value : { status: "unavailable" };
}
