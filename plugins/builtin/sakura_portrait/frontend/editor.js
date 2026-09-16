export function mountEditor({ container, data, host, signal }) {
  const doc = container.ownerDocument;
  const config = structuredClone(data || { default: "", expressions: {} });
  container.classList.add("portrait-plugin");
  const style = new doc.defaultView.CSSStyleSheet();
  style.replaceSync(`
    .portrait-plugin { display: grid; gap: 14px; min-width: 0; }
    .portrait-plugin .expression-row { grid-template-columns: 44px 60px minmax(75px,.6fr) minmax(90px,1fr) auto; padding: 10px; gap: 8px; }
    .portrait-plugin .expression-thumbnail { width: 44px; min-width: 0; height: 52px; padding: 0; overflow: hidden; border: 1px solid var(--hairline); border-radius: 6px; background: var(--sakura-panel-bg); }
    .portrait-plugin .expression-thumbnail img { width: 100%; height: 100%; object-fit: contain; }
    .portrait-plugin .portrait-resource-actions { justify-content: flex-end; }
    .portrait-plugin .portrait-resource-actions .secondary-button { min-width: 64px; padding-inline: 10px; }
    .portrait-plugin .portrait-resource-actions .icon-button { min-width: 32px; padding: 6px; }
    .portrait-plugin .icon-button svg { width: 18px; height: 18px; }
    .portrait-plugin .portrait-preview-dialog { width: min(560px,85vw); max-height: 85vh; border: 1px solid var(--sakura-border); border-radius: 12px; padding: 20px; color: var(--sakura-text); background: var(--sakura-page-bg); }
    .portrait-plugin .portrait-preview-dialog::backdrop { background: #27445a45; }
    .portrait-plugin .portrait-preview-dialog img { width: 100%; max-height: 65vh; object-fit: contain; background: var(--sakura-panel-bg); }
    .portrait-plugin .portrait-preview-dialog h2 { font-size: 16px; margin: 0 0 12px; }
    .portrait-plugin .portrait-preview-dialog footer { display: flex; justify-content: flex-end; margin-top: 14px; }
    @media (max-width:820px) { .portrait-plugin .expression-row { grid-template-columns: 38px 52px minmax(65px,1fr) auto; } .portrait-plugin [data-expression-path] { grid-row: 2; grid-column: 3 / -1; } .portrait-plugin .portrait-resource-actions { grid-column:4; grid-row:1; } }
    @media (max-width:600px) { .portrait-plugin .expression-row { grid-template-columns:44px minmax(0,1fr); } .portrait-plugin [data-expression-label] { grid-row:2;grid-column:1/-1; } .portrait-plugin [data-expression-path] { grid-row:3;grid-column:1/-1; } .portrait-plugin .portrait-resource-actions { grid-row:4;grid-column:1/-1; } }
  `);
  doc.adoptedStyleSheets = [...doc.adoptedStyleSheets, style];
  const releaseStyle = () => { doc.adoptedStyleSheets = doc.adoptedStyleSheets.filter(sheet => sheet !== style); };
  const list = doc.createElement("div");
  list.className = "expression-list";
  const add = doc.createElement("button");
  add.type = "button"; add.className = "secondary-button"; add.textContent = "添加图片";
  const folder = doc.createElement("button");
  folder.type = "button"; folder.className = "secondary-button"; folder.textContent = "导入文件夹";
  const toolbar = doc.createElement("div"); toolbar.className = "resource-section-actions"; toolbar.append(add, folder);
  container.append(toolbar, list);
  let rows = Array.isArray(config.expressionRows) ? config.expressionRows : Object.entries(config.expressions || {}).map(([label, path]) => ({ label, path, selected: path === config.default }));
  if (config.default && !rows.some((row) => row.selected)) {
    const labels = new Set(rows.map(row => row.label.trim()));
    let label = "默认";
    for (let suffix = 2; labels.has(label); suffix++) label = `默认 ${suffix}`;
    rows.unshift({ label, path: config.default, selected: true });
  }
  function collect() {
    const { expressionRows: _draftRows, ...saved } = config;
    const labels = rows.map(row => row.label.trim());
    const result = { ...saved, default: rows.find(row => row.selected)?.path || "" };
    // Unfinished labels cannot be represented losslessly as object keys.
    // Preserve every row across autosave/reopen until the labels are valid.
    if (labels.some(label => !label) || new Set(labels).size !== rows.length) {
      return { ...result, expressionRows: structuredClone(rows) };
    }
    return { ...result, expressions: Object.fromEntries(rows.map((row, index) => [labels[index], row.path])) };
  }
  const changed = () => { if (!signal.aborted) host.changed(collect()); };
  const run = (action) => Promise.resolve().then(action).catch((error) => { if (!signal.aborted) host.error(error); });
  const views = new Map();
  function updateImage(view, row) {
    if (view.source === row.path) return view.ready;
    view.source = row.path;
    view.path.value = row.path.split("/").at(-1);
    const source = row.path;
    view.ready = host.assetUrl(source).then(async url => {
      if (!url || signal.aborted) return false;
      const image = new doc.defaultView.Image();
      image.src = url;
      await image.decode();
      if (signal.aborted || view.source !== source) return false;
      view.preview.src = url;
      return true;
    }).catch(error => {
      if (!signal.aborted && view.source === source) host.error(error, "studio.visual.thumbnail.decode");
      return false;
    });
    return view.ready;
  }
  function render() {
    host.previewImage?.(collect().default);
    for (const [row, view] of views) if (!rows.includes(row)) { view.line.remove(); views.delete(row); }
    for (const row of rows) {
      const existing = views.get(row);
      if (existing) { existing.radio.checked = row.selected; updateImage(existing, row); continue; }
      const line = doc.createElement("div"); line.className = "expression-row";
      const radio = doc.createElement("input"); radio.type = "radio"; radio.name = "portrait-default"; radio.checked = row.selected; radio.setAttribute("aria-label", "默认立绘");
      const defaultControl = doc.createElement("label"); defaultControl.className = "portrait-default-control";
      const defaultText = doc.createElement("span"); defaultText.textContent = "默认"; defaultControl.append(radio, defaultText);
      radio.onchange = () => { for (const item of rows) item.selected = item === row; render(); changed(); };
      const label = doc.createElement("input"); label.type = "text"; label.dataset.expressionLabel = ""; label.value = row.label; label.placeholder = "标签"; label.setAttribute("aria-label", "表情标签");
      label.oninput = () => { row.label = label.value; changed(); };
      const path = doc.createElement("input"); path.type = "text"; path.dataset.expressionPath = ""; path.value = row.path.split("/").at(-1); path.readOnly = true; path.setAttribute("aria-label", "图片文件");
      const thumbnail = doc.createElement("button"); thumbnail.type = "button"; thumbnail.className = "expression-thumbnail"; thumbnail.setAttribute("aria-label", `预览${row.label || "立绘"}`);
      const preview = doc.createElement("img"); preview.alt = ""; thumbnail.append(preview);
      thumbnail.onclick = () => {
        if (!preview.src) return;
        const dialog = doc.createElement("dialog"); dialog.className = "portrait-preview-dialog"; dialog.setAttribute("aria-label", row.label || "立绘预览");
        const heading = doc.createElement("h2"); heading.textContent = row.label || "立绘预览";
        const image = doc.createElement("img"); image.src = preview.src; image.alt = row.label;
        const footer = doc.createElement("footer"); const close = doc.createElement("button"); close.type = "button"; close.className = "secondary-button"; close.textContent = "关闭"; close.onclick = () => dialog.close();
        footer.append(close); dialog.append(heading, image, footer); container.append(dialog); dialog.addEventListener("close", () => dialog.remove(), { once: true }); dialog.showModal();
      };
      const view = { line, radio, path, preview };
      views.set(row, view);
      updateImage(view, row);
      const remove = doc.createElement("button"); remove.type = "button"; remove.className = "icon-button"; remove.setAttribute("aria-label", `移除图片${row.label || "立绘"}`);
      remove.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7"/></svg>';
      remove.onclick = () => { rows = rows.filter((item) => item !== row); if (rows.length && !rows.some((item) => item.selected)) rows[0].selected = true; render(); changed(); };
      const replace = doc.createElement("button"); replace.type = "button"; replace.className = "secondary-button"; replace.textContent = "替换";
      replace.onclick = () => run(async () => { const [file] = await host.importFiles({ multiple: false }); if (!file || signal.aborted) return; if (!file.resourcePath.toLowerCase().endsWith(".png")) throw new Error("请选择 PNG 图片。"); row.path = file.resourcePath; render(); changed(); });
      const actions = doc.createElement("div"); actions.className = "portrait-resource-actions"; actions.append(replace, remove);
      line.append(thumbnail, defaultControl, label, path, actions); list.append(line);
    }
  }
  async function importImages(options) {
    const files = await host.importFiles(options);
    if (signal.aborted) return;
    const descriptions = files.find((file) => file.name === "立绘说明.txt")?.text || files.find((file) => file.name?.toLowerCase() === "description.txt")?.text || "";
    const names = descriptions.split(/\r?\n/).map((line) => { const [name, ...label] = line.trim().split(/\s+/); return [name.toLowerCase(), label.join(" ")]; }).filter(([name, label]) => name && label);
    const stem = (name) => name.replace(/\.[^.]+$/, "").toLowerCase();
    for (const file of files) {
      if (!file.resourcePath.toLowerCase().endsWith(".png")) continue;
      const name = file.name || file.resourcePath.split("/").at(-1);
      const prefix = names.filter(([token]) => stem(name).startsWith(stem(token)));
      const label = names.find(([token]) => token === name.toLowerCase())?.[1] || names.find(([token]) => stem(token) === stem(name))?.[1] || (prefix.length === 1 ? prefix[0][1] : name.replace(/\.[^.]+$/, ""));
      rows.push({ label, path: file.resourcePath, selected: rows.length === 0 });
    }
    render(); changed();
  }
  add.onclick = () => run(() => importImages({ multiple: true }));
  folder.onclick = () => run(() => importImages({ folder: true }));
  render();
  // One decoded thumbnail is enough to replace a frozen editor. Remaining
  // previews load independently and never hold editing or saving behind them.
  const ready = Promise.any([...views.values()].map(view => view.ready.then(loaded => {
    if (!loaded) throw new Error("PORTRAIT_PREVIEW_UNAVAILABLE");
  }))).catch(() => {});
  return { ready, collect, validate() {
    if (!rows.length || !rows.some((row) => row.selected)) throw new Error("请选择默认立绘。");
    const labels = rows.map((row) => row.label.trim());
    if (labels.some((label) => !label) || new Set(labels).size !== rows.length) throw new Error("表情标签不能为空或重复。");
    return true;
  }, destroy() { releaseStyle(); container.replaceChildren(); } };
}
