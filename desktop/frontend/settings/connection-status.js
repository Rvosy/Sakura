// A connection's current state and available actions, owned by the plugin.
export function createConnectionStatus({ document, section, read, renderDisplay, renderAction, busy = () => false }) {
  const root = document.createElement("section"); root.className = "connection-status";
  let signature;
  function update() {
    const p = section.presentation;
    const status = read(p.statusField), image = read(p.imageField), available = read(p.actionsField) || [];
    const next = JSON.stringify([status, image, available, busy()]);
    if (next === signature) return;
    signature = next; root.replaceChildren();
    root.classList.toggle("is-linking", Boolean(image) || status?.state === "working");
    const header = document.createElement("div"); header.className = "connection-status-header";
    const title = document.createElement("h3"); title.textContent = section.title;
    header.append(title, renderDisplay(section.fields.find(f => f.key === p.statusField)));
    const actions = document.createElement("div"); actions.className = "plugin-setting-actions";
    for (const action of section.actions.filter(a => available.includes(a.action_id))) {
      const button = renderAction(action); button.disabled = busy(); actions.append(button);
    }
    root.append(header);
    if (image) {
      const visual = document.createElement("div"); visual.className = "connection-status-visual";
      visual.append(renderDisplay(section.fields.find(f => f.key === p.imageField)));
      const caption = document.createElement("p"); caption.textContent = image.alt; visual.append(caption); root.append(visual);
    }
    root.append(actions);
  }
  update();
  return { element: root, update, dispose() { root.remove(); } };
}
