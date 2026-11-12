import { FALLBACK_THEME_TOKENS } from "../../core/theme.js";

const frame = document.getElementById("preview");
const mode = document.getElementById("mode");
const theme = document.getElementById("theme");
const status = document.getElementById("status");
const scenario = document.getElementById("scenario");
const params = new URLSearchParams(location.search);
if (params.get("scenario") === "multiple") scenario.value = "multiple";
if (params.get("mode") === "manual") mode.value = "manual";
const dark = { primary: "#8dc8e9", primaryHover: "#aedcf4", accent: "#ee9ab7", text: "#e2ecf5", secondaryText: "#a9bcd1", mutedText: "#8196ae", pageBackground: "#1c2532", panelBackground: "#243142", inputBackground: "#18212c", bubbleBackground: "#2c3c50", border: "#465c73" };
const { versions } = await (await fetch("../../data/whats-new.json")).json();
window.previewBridge = async (command, args) => {
  switch (command) {
    case "whats_new_bootstrap": return {
      version: versions[0].version,
      seenVersion: scenario.value === "multiple" && mode.value === "startup" ? "1.3.0" : null,
      entries: previewEntries(),
      themeTokens: theme.value === "dark" ? dark : FALLBACK_THEME_TOKENS,
    };
    case "resize_whats_new": frame.style.height = `${Math.min(args.height, 620)}px`; return;
    case "reveal_whats_new": frame.style.visibility = "visible"; return;
    case "close_whats_new": frame.hidden = true; status.textContent = "摘要已关闭，可点击“重新打开”继续查看。"; return;
    default: throw new Error(`未知预览命令：${command}`);
  }
};
function previewEntries() {
  if (scenario.value === "single") return versions.slice(0, 1);
  return mode.value === "startup"
    ? versions.slice(0, versions.findIndex(entry => entry.version === "1.3.0"))
    : versions;
}
async function load() {
  frame.hidden = false;
  status.textContent = "";
  document.body.classList.toggle("dark", theme.value === "dark");
  frame.style.visibility = "hidden";
  frame.src = `./frame.html?${mode.value === "startup" ? "startup=1" : "manual=1"}`;
}
for (const control of [mode, theme, scenario]) control.addEventListener("change", () => load().catch(error => { status.textContent = String(error); }));
document.getElementById("reopen").addEventListener("click", () => load().catch(error => { status.textContent = String(error); }));
await load();
