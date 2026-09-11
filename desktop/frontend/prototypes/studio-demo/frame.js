import { createStudioBridge } from "./bridge.js";
import { createStudioSurface } from "./surface.js";

const review = parent.__STUDIO_REVIEW__;
const surface = createStudioSurface(review.state);
window.__STUDIO_DEMO_SURFACE__ = surface;
createStudioBridge(review.state, {
  ready() { surface.showForms(); review.ready(); },
  close: review.close,
});
window.addEventListener("error", event => review.failed(`工坊载入失败：${event.message}`));
window.addEventListener("unhandledrejection", event => review.failed(`工坊操作失败：${String(event.reason)}`));
const path = new URL("../../studio/studio.js", import.meta.url);
let source = await (await fetch(path, { cache: "no-store" })).text();
source = source.replace(/(["'])(\.\.?\/[^"'\n]+)\1/g, (_match, quote, value) => quote + new URL(value, path).href + quote);
function replaceOnce(from, to) {
  if (!source.includes(from)) throw Error(`工坊 Demo 接入点已变化：${from}`);
  source = source.replace(from, to);
}
replaceOnce('portrait: { title: "立绘" }', 'portrait: { title: "角色形态" }');
replaceOnce("    default_portrait: defaultPortrait,\n    expressions,", "    ...window.__STUDIO_DEMO_SURFACE__.collectPortrait(expressions, defaultPortrait),");
replaceOnce('  renderExpressions(doc.expressions || {}, doc.default_portrait || "");', "  window.__STUDIO_DEMO_SURFACE__.render(doc, renderExpressions);");
replaceOnce("function handleEditorChanged() {", "function handleEditorChanged() {\n  if (!renderingEditor) window.__STUDIO_DEMO_SURFACE__.refreshPreview();");
replaceOnce("function validateExpressionInputs() {", "function validateExpressionInputs() {\n  return window.__STUDIO_DEMO_SURFACE__.validate();\n}\nfunction legacyValidateExpressionInputs() {");
replaceOnce('  fields.themeFields.querySelectorAll("input, button").forEach((element) => {\n    element.disabled = busy || !hasDoc;\n  });', '  fields.themeFields.querySelectorAll("input, button").forEach((element) => {\n    element.disabled = busy || !hasDoc;\n  });\n  window.__STUDIO_DEMO_SURFACE__.setBusy(busy || !hasDoc);');
replaceOnce('startStudio().catch((error) => setError(String(error)));', 'window.__STUDIO_DEMO_SURFACE__.attach({collect: collectDoc, changed: handleEditorChanged, flush: flushDraftAutosave, notify, setError, switchPage});\nstartStudio().catch((error) => setError(String(error)));');
const blob = URL.createObjectURL(new Blob([source], { type: "text/javascript" }));
await import(blob);
URL.revokeObjectURL(blob);
