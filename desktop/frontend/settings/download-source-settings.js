export async function openDownloadSources({ document, invoke, notify }) {
  if (document.getElementById("download-sources-dialog")) return;
  const sources = await invoke("settings_download_sources_get");
  const dialog = document.createElement("dialog");
  dialog.id = "download-sources-dialog"; dialog.className = "download-sources-dialog";
  dialog.setAttribute("aria-label", "下载源");
  const heading = document.createElement("h2"); heading.textContent = "下载源"; dialog.append(heading);
  const rows = document.createElement("div"); dialog.append(rows);
  const message = document.createElement("p"); message.setAttribute("role", "status"); message.hidden = true; dialog.append(message);
  function render() {
    rows.replaceChildren();
    sources.forEach((source, index) => {
      const row = document.createElement("div"); row.className = "download-source-row";
      const enabled = document.createElement("input"); enabled.type = "checkbox"; enabled.checked = source.enabled;
      enabled.setAttribute("aria-label", `启用 ${source.name}`); enabled.onchange = () => { source.enabled = enabled.checked; };
      const name = document.createElement("input"); name.type = "text"; name.value = source.name; name.setAttribute("aria-label", "名称");
      name.oninput = () => { source.name = name.value; };
      const prefix = document.createElement("input"); prefix.value = source.prefix; prefix.type = "text"; prefix.inputMode = "url"; prefix.className = "download-source-prefix";
      prefix.placeholder = "官方源留空"; prefix.setAttribute("aria-label", "镜像前缀"); prefix.oninput = () => { source.prefix = prefix.value.trim(); };
      row.append(enabled, name);
      const controls = document.createElement("div"); controls.className = "download-source-controls";
      for (const [label, direction] of [["上移", -1], ["下移", 1]]) {
        const button = document.createElement("button"); button.textContent = direction < 0 ? "↑" : "↓"; button.setAttribute("aria-label", `${label} ${source.name}`); button.className = "secondary-button";
        button.disabled = index + direction < 0 || index + direction >= sources.length;
        button.onclick = () => { [sources[index], sources[index + direction]] = [sources[index + direction], sources[index]]; render(); };
        controls.append(button);
      }
      const remove = document.createElement("button"); remove.textContent = "移除"; remove.className = "secondary-button";
      remove.onclick = () => { sources.splice(index, 1); render(); }; controls.append(remove); row.append(controls, prefix); rows.append(row);
    });
  }
  const actions = document.createElement("div"); actions.className = "download-source-actions";
  const add = document.createElement("button"); add.textContent = "添加镜像"; add.className = "secondary-button";
  add.onclick = () => { sources.push({ name: "自定义", prefix: "https://", enabled: true }); render(); };
  const close = document.createElement("button"); close.textContent = "取消"; close.className = "secondary-button"; close.onclick = () => dialog.close();
  const save = document.createElement("button"); save.textContent = "保存";
  save.onclick = async () => {
    save.disabled = true;
    try { await invoke("settings_download_sources_save", { value: sources }); dialog.close(); notify("已保存下载源", "success"); }
    catch (error) { message.hidden = false; message.textContent = String(error.message || error); }
    finally { save.disabled = false; }
  };
  actions.append(add, close, save); dialog.append(actions); dialog.addEventListener("close", () => dialog.remove(), { once: true });
  render(); document.body.append(dialog); dialog.showModal();
}
