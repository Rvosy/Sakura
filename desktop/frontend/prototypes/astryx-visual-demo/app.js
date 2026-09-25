import { createState } from "../settings-demo/data.js";

const frame = document.getElementById("settings");
const loading = document.getElementById("loading");

let mode = "visual";
let state = createState("portrait");

window.__SAKURA_ASTRYX_VISUAL_DEMO__ = {
  get state() {
    return state;
  },
  get mode() {
    return mode;
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
  loading.textContent = "正在载入当前 main 设置页…";
  loading.hidden = false;

  try {
    const source = await (await fetch("../../settings/index.html")).text();
    const doc = new DOMParser().parseFromString(source, "text/html");

    const base = doc.createElement("base");
    base.href = new URL("../../settings/", location.href).href;
    doc.head.prepend(base);
    doc.title = mode === "visual" ? "Sakura 设置 · 视觉 Demo" : "Sakura 设置 · main 对照";

    doc.querySelector('script[src="./settings.js"]')?.remove();

    const support = doc.createElement("link");
    support.rel = "stylesheet";
    support.href = new URL("./support.css", location.href).href;
    doc.head.append(support);

    if (mode === "visual") {
      const visual = doc.createElement("link");
      visual.rel = "stylesheet";
      visual.href = new URL("./visual.css", location.href).href;
      doc.head.append(visual);
    }

    const script = doc.createElement("script");
    script.type = "module";
    script.src = new URL("./frame.js", location.href).href;
    doc.body.append(script);

    frame.srcdoc = "<!doctype html>\n" + doc.documentElement.outerHTML;
  } catch (error) {
    loading.textContent = "设置载入失败：" + error.message;
  }
}

for (const id of ["visual", "main"]) {
  document.getElementById(id).onclick = () => {
    mode = id;
    for (const other of ["visual", "main"]) {
      const active = other === id;
      document.getElementById(other).classList.toggle("selected", active);
      document.getElementById(other).setAttribute("aria-pressed", String(active));
    }
    load();
  };
}

document.getElementById("scenario").onchange = (event) => {
  state = createState(event.target.value);
  load();
};

document.getElementById("reset").onclick = () => {
  state = createState(document.getElementById("scenario").value);
  load();
};

await load();
