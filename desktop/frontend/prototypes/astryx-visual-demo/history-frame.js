import { loadHistory } from "./history-data.js";
import { theme } from "../settings-demo/data.js";
const review = parent.__SAKURA_VISUAL_REVIEW__;
let history = await loadHistory();
window.__SAKURA_HISTORY__ = history;
const identity = () => ({ coreGenerationId: "history-demo", characterId: history.characterId });
window.__TAURI__ = {
  core: { invoke: async name => {
    if (name === "history_bootstrap") {
      history = await loadHistory();
      window.__SAKURA_HISTORY__ = history;
      window.dispatchEvent(new Event("review-theme"));
      return {...identity(), assistantName: history.assistantName, subtitleLanguage: "zh", themeTokens: theme};
    }
    if (name === "history_page") return {...identity(), schemaVersion:1, entries:history.entries, totalCount:history.entries.length, hasMore:false, beforeCursor:null};
    if (name === "close_history_window") review.update({page:"settings"});
    return null;
  } },
  event: { listen: async () => () => {} },
};
await import("../../history/history.js");
await import("./dist/components.js");
