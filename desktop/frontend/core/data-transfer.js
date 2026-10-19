export function createDataTransferButtons({ document, invoke, characterId, identityKey = characterId, isCurrent = () => true,
  transfer, onImported = () => {}, history = false }) {
  const group = document.createElement("span");
  group.className = "data-transfer-buttons";
  for (const [action, label] of [["import", "导入"], ["export", "导出"]]) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "button button-secondary secondary-button";
    button.textContent = label;
    button.addEventListener("click", () => open(action));
    group.append(button);
  }
  function open(action) {
    const expectedCharacterId = characterId();
    const expectedIdentity = identityKey();
    if (!expectedCharacterId || !isCurrent()) return;
    const current = () => isCurrent() && expectedIdentity === identityKey();
    const dialog = document.createElement("dialog");
    dialog.className = "data-transfer-dialog";
    dialog.innerHTML = `<h2>${action === "export" ? "导出" : "导入"}${history ? "聊天记录" : "记忆"}</h2>
      <p data-description></p><div data-options></div>
      <p data-result role="status" aria-live="polite"></p>
      <footer><button type="button" data-close>关闭</button><button type="button" data-run></button></footer>`;
    const description = dialog.querySelector("[data-description]");
    const options = dialog.querySelector("[data-options]");
    const result = dialog.querySelector("[data-result]");
    const run = dialog.querySelector("[data-run]");
    const exporting = action === "export";
    description.textContent = exporting
      ? "Sakura JSON 用于迁移和恢复；TXT 方便查看和分享，不能完整恢复。"
      : "导入到当前角色。重复记录跳过，冲突保留本地内容。";
    let format = "json";
    if (exporting) {
      const select = document.createElement("select");
      select.setAttribute("aria-label", "导出格式");
      select.innerHTML = '<option value="json">Sakura JSON</option><option value="txt">纯文本 TXT</option>';
      select.addEventListener("change", () => { format = select.value; });
      options.append(select);
      if (history) {
        for (const [name, label] of [["startDate", "开始日期"], ["endDate", "结束日期"]]) {
          const field = document.createElement("label");
          field.textContent = label;
          const input = document.createElement("input");
          input.type = "date";
          input.name = name;
          field.append(input);
          options.append(field);
        }
      }
    }
    run.textContent = exporting ? "选择保存位置" : "选择文件";
    let path = null;
    dialog.querySelector("[data-close]").addEventListener("click", () => dialog.close());
    const dispose = () => dialog.close();
    document.defaultView?.addEventListener("pagehide", dispose, { once: true });
    dialog.addEventListener("close", () => {
      document.defaultView?.removeEventListener("pagehide", dispose);
      dialog.remove();
    }, { once: true });
    run.addEventListener("click", async () => {
      if (!current()) {
        result.textContent = "角色或插件已变化，请关闭后重新打开。";
        return;
      }
      run.disabled = true;
      try {
        const importing = !exporting && path !== null;
        if (!importing) path = await invoke("data_transfer_choose", { export: exporting, format });
        if (!path || !dialog.isConnected || !current()) return;
        const request = { action: exporting ? "export" : importing ? "import" : "preview",
          path, format, expectedCharacterId };
        if (exporting && history) {
          request.startDate = options.querySelector('[name="startDate"]').value;
          request.endDate = options.querySelector('[name="endDate"]').value;
        }
        result.textContent = importing ? "正在导入…" : exporting ? "正在导出…" : "正在读取文件…";
        const outcome = await transfer(request);
        if (importing && current()) { path = null; await onImported(); }
        if (!dialog.isConnected || !current()) return;
        if (exporting) result.textContent = `已导出 ${outcome.exported} 条记录。`;
        else {
          const source = outcome.sourceCharacterId !== expectedCharacterId
            ? `从角色 ${outcome.sourceCharacterId} 复制到 ${expectedCharacterId}。` : "";
          result.textContent = `${source}${importing ? "已导入" : "可新增"} ${outcome.added} 条，重复 ${outcome.identical} 条，冲突保留本地 ${outcome.conflicts} 条。`;
          run.textContent = importing ? "选择文件" : "确认导入";
        }
      } catch (error) {
        if (dialog.isConnected) result.textContent = String(error?.message || error);
        path = null;
        run.textContent = exporting ? "选择保存位置" : "选择文件";
      } finally { run.disabled = false; }
    });
    document.body.append(dialog);
    dialog.showModal();
  }
  return group;
}
