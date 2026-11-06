const frame = document.getElementById("settings");
window.settingsMotionPreview = {
  enabled: true,
  outcome: "success",
  setMode: null,
};
for (const id of ["original", "enhanced"]) {
  document.getElementById(id).addEventListener("click", () => {
    window.settingsMotionPreview.enabled = id === "enhanced";
    for (const key of ["original", "enhanced"]) document.getElementById(key).setAttribute("aria-pressed", String(key === id));
    window.settingsMotionPreview.setMode?.(window.settingsMotionPreview.enabled);
  });
}
document.getElementById("outcome").addEventListener("change", event => {
  window.settingsMotionPreview.outcome = event.target.value;
});
document.getElementById("demoIndex").addEventListener("change", event => {
  if (event.target.value) window.settingsMotionPreview.showDemo?.(event.target.value);
});
try {
  const response = await fetch("../../settings/index.html");
  const doc = new DOMParser().parseFromString(await response.text(), "text/html");
  const base = doc.createElement("base");
  base.href = new URL("../../settings/", location.href).href;
  doc.head.prepend(base);
  doc.querySelector('script[src="./settings.js"]').remove();
  const styles = doc.createElement("link");
  styles.rel = "stylesheet";
  styles.href = new URL("./motion.css", location.href).href;
  doc.head.append(styles);
  const script = doc.createElement("script");
  script.type = "module";
  script.src = new URL("./frame.js", location.href).href;
  doc.body.append(script);
  frame.srcdoc = "<!doctype html>\n" + doc.documentElement.outerHTML;
} catch (error) {
  const message = document.getElementById("loadError");
  message.hidden = false;
  message.textContent = `设置页载入失败：${error.message}`;
}
