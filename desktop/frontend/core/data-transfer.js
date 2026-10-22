import { createIcon } from "./icons.js";
import { createDateRangePicker } from "./date-range-picker.js";

let nextId = 0;

export function createDataTransferMenu({ document, invoke, characterId, identityKey = characterId, isCurrent = () => true,
  transfer, onImported = () => {}, history = false, loadDates }) {
  const id = `data-transfer-${++nextId}`;
  const subject = history ? "聊天记录" : "记忆";
  const group = document.createElement("span");
  group.className = "data-transfer-menu";
  const trigger = document.createElement("button");
  trigger.type = "button";
  trigger.className = "data-transfer-trigger";
  trigger.append(createIcon(document, "ellipsis"));
  trigger.title = `${subject}更多操作`;
  trigger.setAttribute("aria-label", trigger.title);
  trigger.setAttribute("aria-haspopup", "menu");
  trigger.setAttribute("aria-expanded", "false");
  const menu = document.createElement("div");
  menu.id = `${id}-menu`;
  menu.className = "data-transfer-actions";
  menu.setAttribute("popover", "auto");
  menu.setAttribute("role", "menu");
  menu.setAttribute("aria-label", `${subject}操作`);
  trigger.setAttribute("aria-controls", menu.id);
  trigger.popoverTargetElement = menu;
  for (const [action, label] of [["export", `导出${subject}…`], ["import", `导入${subject}…`]]) {
    const button = document.createElement("button");
    button.type = "button";
    button.setAttribute("role", "menuitem");
    button.append(createIcon(document, action === "export" ? "download" : "folder"), label);
    button.addEventListener("click", () => {
      menu.hidePopover();
      open(action);
    });
    menu.append(button);
  }
  menu.addEventListener("beforetoggle", event => {
    trigger.setAttribute("aria-expanded", String(event.newState === "open"));
    if (event.newState !== "open") return;
    const bounds = trigger.getBoundingClientRect();
    menu.style.left = `${Math.max(8, Math.min(bounds.right - 180, document.documentElement.clientWidth - 188))}px`;
    menu.style.top = `${bounds.bottom + 6}px`;
  });
  trigger.addEventListener("keydown", event => {
    if (!["ArrowDown", "ArrowUp"].includes(event.key)) return;
    event.preventDefault();
    menu.showPopover();
    (event.key === "ArrowUp" ? menu.lastElementChild : menu.firstElementChild).focus();
  });
  menu.addEventListener("keydown", event => {
    const items = [...menu.children];
    const index = items.indexOf(document.activeElement);
    if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
      event.preventDefault();
      const next = event.key === "Home" ? 0 : event.key === "End" ? items.length - 1
        : (index + (event.key === "ArrowUp" ? -1 : 1) + items.length) % items.length;
      items[next].focus();
    } else if (event.key === "Escape") {
      event.preventDefault();
      menu.hidePopover();
      trigger.focus();
    } else if (event.key === "Tab") {
      menu.hidePopover();
      trigger.focus();
    }
  });
  group.append(trigger, menu);

  function open(action) {
    const expectedCharacterId = characterId();
    const expectedIdentity = identityKey();
    if (!expectedCharacterId || !isCurrent()) return;
    const currentIdentity = () => isCurrent() && expectedIdentity === identityKey();
    const dialog = document.createElement("dialog");
    const current = () => dialog.open && dialog.isConnected && currentIdentity();
    const exporting = action === "export";
    dialog.className = "data-transfer-dialog";
    dialog.setAttribute("aria-labelledby", `${id}-title`);
    dialog.setAttribute("aria-describedby", `${id}-description`);
    dialog.innerHTML = `<h2 id="${id}-title" tabindex="-1" autofocus>${exporting ? "导出" : "导入"}${subject}</h2>
      <p id="${id}-description" class="data-transfer-description"></p>
      <fieldset data-options></fieldset>
      <section class="data-transfer-preview" hidden>
        <p data-file></p><p data-source></p><dl data-counts></dl>
      </section>
      <p data-result role="status" aria-live="polite"></p>
      <footer><button type="button" data-rechoose hidden>更换文件</button><span></span>
        <button type="button" data-close>关闭</button><button type="button" data-run></button></footer>`;
    const options = dialog.querySelector("[data-options]");
    const preview = dialog.querySelector(".data-transfer-preview");
    const result = dialog.querySelector("[data-result]");
    const run = dialog.querySelector("[data-run]");
    const rechoose = dialog.querySelector("[data-rechoose]");
    dialog.querySelector(".data-transfer-description").textContent = exporting
      ? `导出当前角色的${subject}。`
      : "选择 Sakura JSON 文件，预览后合并到当前角色。重复记录跳过，冲突保留本地内容。";
    let format = "json";
    let dates;
    if (exporting) {
      const formats = document.createElement("fieldset");
      formats.className = "data-transfer-formats";
      formats.innerHTML = "<legend>文件用途</legend>";
      for (const [value, title, detail] of [
        ["json", "迁移与恢复", "Sakura JSON · 保留完整记录，可再次导入"],
        ["txt", "阅读与分享", "TXT 纯文本 · 方便查看，不支持导入恢复"],
      ]) {
        const choice = document.createElement("label");
        choice.className = "data-transfer-format";
        const radio = document.createElement("input");
        radio.type = "radio";
        radio.name = `${id}-format`;
        radio.value = value;
        radio.checked = value === format;
        radio.setAttribute("aria-label", title);
        const copy = document.createElement("span");
        const heading = document.createElement("strong");
        heading.textContent = title;
        const hint = document.createElement("small");
        hint.textContent = detail;
        copy.append(heading, hint);
        choice.append(radio, copy);
        radio.addEventListener("change", () => { format = value; result.textContent = ""; });
        formats.append(choice);
      }
      options.append(formats);
      if (history) {
        dates = createDateRangePicker({ document, id, isCurrent: current, loadDates,
          loadRange: () => transfer({ action: "range", expectedCharacterId }),
          onChange: () => { result.textContent = ""; } });
        options.append(dates.element);
      }
    } else options.hidden = true;
    const initialLabel = exporting ? "选择保存位置" : "选择文件";
    run.textContent = initialLabel;
    let path = null;
    dialog.querySelector("[data-close]").addEventListener("click", () => dialog.close());
    const dispose = () => { dialog.close(); dialog.remove(); dates?.dispose(); };
    document.defaultView?.addEventListener("pagehide", dispose, { once: true });
    dialog.addEventListener("close", async () => {
      document.defaultView?.removeEventListener("pagehide", dispose);
      dates?.dispose();
      await Promise.allSettled(dialog.getAnimations().map(animation => animation.finished));
      dialog.remove();
      if (trigger.isConnected) trigger.focus({ preventScroll: true });
    }, { once: true });

    async function execute(chooseNew = false) {
      if (!currentIdentity()) {
        result.textContent = "角色或插件已变化，请关闭后重新打开。";
        return;
      }
      run.disabled = true;
      rechoose.disabled = true;
      options.disabled = true;
      dialog.setAttribute("aria-busy", "true");
      try {
        const importing = !exporting && path !== null && !chooseNew;
        if (!importing) {
          const selected = await invoke("data_transfer_choose", { export: exporting, format,
            defaultName: `${history ? "history" : "memory"}_${expectedCharacterId}.${format}` });
          if (!selected || !current()) return;
          path = selected;
        }
        const request = { action: exporting ? "export" : importing ? "import" : "preview",
          path, format, expectedCharacterId };
        if (exporting && history) {
          Object.assign(request, dates.getValue());
        }
        preview.hidden = true;
        result.textContent = importing ? "正在导入…" : exporting ? "正在导出…" : "正在读取文件…";
        const outcome = await transfer(request);
        if (!current()) return;
        if (importing) {
          path = null;
          await onImported();
          if (!current()) return;
        }
        if (exporting) result.textContent = `已导出 ${outcome.exported} 条记录。`;
        else {
          preview.hidden = false;
          dialog.querySelector("[data-file]").textContent = request.path.split(/[\\/]/).pop();
          dialog.querySelector("[data-source]").textContent = outcome.sourceCharacterId !== expectedCharacterId
            ? `从角色 ${outcome.sourceCharacterId} 复制到 ${expectedCharacterId}`
            : "合并到当前角色";
          const counts = dialog.querySelector("[data-counts]");
          counts.replaceChildren();
          for (const [label, value] of [[importing ? "已导入" : "可新增", outcome.added],
            ["重复跳过", outcome.identical], ["冲突保留本地", outcome.conflicts]]) {
            const item = document.createElement("div");
            const term = document.createElement("dt");
            const number = document.createElement("dd");
            term.textContent = label;
            number.textContent = value;
            item.append(term, number);
            counts.append(item);
          }
          result.textContent = importing ? `已导入 ${outcome.added} 条记录。` : "核对以上内容后确认导入。";
          run.textContent = importing ? "选择文件" : "确认导入";
          rechoose.hidden = importing;
        }
      } catch (error) {
        if (!current()) return;
        result.textContent = String(error?.message || error);
        path = null;
        rechoose.hidden = true;
        run.textContent = initialLabel;
      } finally {
        run.disabled = false;
        rechoose.disabled = false;
        options.disabled = false;
        dialog.removeAttribute("aria-busy");
      }
    }
    run.addEventListener("click", () => void execute());
    rechoose.addEventListener("click", () => void execute(true));
    document.body.append(dialog);
    dialog.showModal();
  }
  return group;
}
