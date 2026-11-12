const source = await (await fetch("../../whats-new/index.html")).text();
const doc = new DOMParser().parseFromString(source, "text/html");
const base = document.createElement("base");
base.href = new URL("../../whats-new/", location.href).href;
doc.head.prepend(base);
doc.querySelector('script[src="./whats-new.js"]').remove();
document.head.replaceChildren(...doc.head.childNodes);
document.body.replaceChildren(...doc.body.childNodes);
window.__TAURI__ = {
  core: { invoke: (command, args) => {
    if (command === "whats_new_open_details") {
      const base = "https://github.com/Rvosy/Sakura/releases";
      window.open(args.version ? `${base}/tag/v${encodeURIComponent(args.version)}` : base, "_blank", "noopener");
      return Promise.resolve();
    }
    return parent.previewBridge(command, args);
  } },
  event: { listen: async () => () => {} },
};
await import("../../whats-new/whats-new.js");
