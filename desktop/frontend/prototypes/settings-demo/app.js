import { createState } from "./data.js";
const frame = document.getElementById("settings"),
  loading = document.getElementById("loading");
let version = "proposed";
let state = createState("portrait");
window.__SAKURA_SETTINGS_REVIEW__ = {
  get state() {
    return state;
  },
  get version() {
    return version;
  },
  ready() {
    loading.hidden = true;
  },
  failed(message) {
    loading.textContent = message;
    loading.hidden = false;
  },
  close() {
    loading.replaceChildren();
    const box = document.createElement("div");
    box.className = "closed-state";
    const text = document.createElement("p");
    text.textContent = "设置已关闭";
    const button = document.createElement("button");
    button.textContent = "重新打开设置";
    button.onclick = () => load();
    box.append(text, button);
    loading.append(box);
    loading.hidden = false;
  },
};
async function load() {
  loading.textContent = "正在载入设置…";
  loading.hidden = false;
  try {
    const source = await (await fetch("../../settings/index.html")).text();
    const doc = new DOMParser().parseFromString(source, "text/html");
    const base = doc.createElement("base");
    base.href = new URL("../../settings/", location.href).href;
    doc.head.prepend(base);
    doc.title = "Sakura 设置";
    doc.querySelector('script[src="./settings.js"]').remove();
    const css = doc.createElement("link");
    css.rel = "stylesheet";
    css.href = new URL("./surface.css", location.href).href;
    doc.head.append(css);
    const script = doc.createElement("script");
    script.type = "module";
    script.src = new URL("./frame.js", location.href).href;
    doc.body.append(script);
    frame.srcdoc = "<!doctype html>\n" + doc.documentElement.outerHTML;
  } catch (error) {
    loading.textContent = "设置载入失败：" + error.message;
  }
}
for (const id of ["proposed", "original"])
  document.getElementById(id).onclick = () => {
    version = id;
    for (const other of ["proposed", "original"]) {
      document.getElementById(other).classList.toggle("selected", other === id);
      document
        .getElementById(other)
        .setAttribute("aria-pressed", String(other === id));
    }
    load();
  };
document.getElementById("scenario").onchange = (event) => {
  state = createState(event.target.value);
  load();
};
document.getElementById("reset").onclick = () => {
  state = createState(document.getElementById("scenario").value);
  load();
};
await load();
