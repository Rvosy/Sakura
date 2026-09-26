import { FALLBACK_THEME_TOKENS } from "../../core/theme.js";
import { createSnapshot } from "../../runtime-log-demo/fixtures.js";

// Both views use the current viewer and the same synthetic records.
const snapshot = createSnapshot("proposed", "api");
for (const scope of ["tts", "plugins"]) {
  snapshot.records.push(...createSnapshot("proposed", scope).records);
}
snapshot.records.forEach((record, index) => { record.sequence = index + 1; });
snapshot.latestSequence = snapshot.records.length;
snapshot.runId = "astryx-visual-review";
window.__TAURI__ = {
  core: { invoke: async command => {
    if (command === "runtime_log_viewer_bootstrap") return {schemaVersion:3, themeTokens:FALLBACK_THEME_TOKENS, snapshot:structuredClone(snapshot)};
    if (command === "runtime_log_viewer_snapshot") return {...snapshot, resetRequired:false, records:[]};
    if (command === "reveal_runtime_log_viewer") return;
    if (command === "close_runtime_log_viewer") return parent.__SAKURA_VISUAL_REVIEW__.update({page:"settings"});
    throw new Error(`Unsupported demo command: ${command}`);
  } },
  event: { listen: async () => () => {} },
};
document.getElementById("copy").addEventListener("click", event => {
  if (parent.__SAKURA_VISUAL_REVIEW__.options.version === "original") return;
  event.stopImmediatePropagation();
  const text = event.currentTarget.dataset.copyText;
  if (text) window.__SAKURA_DEMO_INFO__("复制内容预览", text);
}, true);
await import("../../runtime-log/runtime-log.js");
await import("./dist/components.js");
