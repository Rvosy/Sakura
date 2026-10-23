import { createIcon } from "../core/icons.js";

// 插件提供记录和权限，宿主负责展示与草稿编辑。
export function createRecordTable({ document, read, write, presentation, inspect, inspectLabel, scope = "records",
  onError, enhanceSelect, refreshSelect, closeSelects }) {
  const node = (tag, className = "", text = "") => {
    const element = document.createElement(tag); element.className = className; element.textContent = text; return element;
  };
  const root = node("div", "record-table");
  const content = node("div", "record-workbench");
  const sidebar = node("div", "record-sidebar");
  const toolbar = node("div", "record-toolbar");
  const searchBox = node("div", "record-search");
  const search = node("input", "admin-search"); search.type = "text"; search.placeholder = "搜索名称、房间或别名";
  search.setAttribute("aria-label", "搜索记录"); searchBox.append(createIcon(document, "search"), search);
  const count = node("span", "record-result-count"); count.setAttribute("role", "status");
  toolbar.append(searchBox, count); sidebar.append(toolbar);
  const list = node("div", "record-list"); list.setAttribute("aria-label", "记录列表");
  const editor = node("section", "record-editor"); sidebar.append(list); content.append(sidebar, editor); root.append(content);
  let selectedId = "";
  let signature = "", disposed = false, pendingId = null, details = null, generation = 0;
  const controls = new Map();
  const summaries = new Map();
  const currentItems = () => read(presentation.itemsField) || [];
  const currentValues = () => read(presentation.valueField) || {};
  const summaryColumn = presentation.columns.find(column => !column.readonly && ["select", "boolean"].includes(column.type));
  function updateSummary(item) {
    const label = summaries.get(item.id);
    if (!label || !summaryColumn) return;
    const value = currentValues()[item.id]?.[summaryColumn.key] ?? summaryColumn.default;
    label.textContent = summaryColumn.type === "boolean" ? `${summaryColumn.label}：${value ? "是" : "否"}`
      : summaryColumn.options.find(option => option.value === value)?.label || "";
  }
  function closeDetails() { details?.remove(); details = null; }
  function showDetails(result) {
    closeDetails();
    details = node("section", "record-details"); details.setAttribute("role", "region"); details.setAttribute("aria-label", result.title);
    const heading = node("div", "record-details-heading"); heading.append(node("h3", "", result.title));
    const close = node("button", "record-details-close", "收起"); close.type = "button"; close.addEventListener("click", closeDetails); heading.append(close); details.append(heading);
    if (result.message) details.append(node("p", "hint", result.message));
    const renderRows = (target, rows) => {
      let group = null, list;
      for (const row of rows) {
        if (!list || (row.group || "") !== group) {
          group = row.group || "";
          if (group) target.append(node("h4", "", group));
          list = node("dl", "record-state-values"); target.append(list);
        }
        list.append(node("dt", "", row.label), node("dd", "", row.value));
      }
    };
    renderRows(details, (result.rows || []).filter(row => !row.secondary));
    const secondary = (result.rows || []).filter(row => row.secondary);
    if (secondary.length) {
      const extra = node("details", "record-secondary");
      extra.append(node("summary", "", `其他状态（${secondary.length}）`)); renderRows(extra, secondary); details.append(extra);
    }
    editor.append(details); details.scrollIntoView({ block: "nearest" });
  }
  async function inspectItem(item, button) {
    if (pendingId !== null) return;
    pendingId = item.id; button.disabled = true; button.textContent = "正在读取…";
    const ticket = generation;
    try {
      const result = await inspect(item.id, () => !disposed && ticket === generation);
      if (!disposed && ticket === generation && currentItems().some(record => record.id === item.id) && result?.id === item.id) showDetails(result);
    } catch (error) { if (!disposed && ticket === generation) onError(error); }
    finally {
      pendingId = null;
      if (!disposed) {
        editor.querySelectorAll(".record-inspect").forEach(control => { control.disabled = false; });
        button.textContent = inspectLabel;
      }
    }
  }
  function render() {
    closeSelects(editor); controls.clear(); summaries.clear(); list.replaceChildren(); editor.replaceChildren(); closeDetails();
    toolbar.hidden = currentItems().length === 0;
    const query = search.value.trim().toLocaleLowerCase();
    const values = currentValues();
    const items = currentItems().filter(item => [item.label, item.description, ...Object.values(values[item.id] || {})].join(" ").toLocaleLowerCase().includes(query));
    count.textContent = query ? `${items.length} / ${currentItems().length}` : `共 ${items.length} 项`;
    if (!items.some(item => item.id === selectedId)) selectedId = items[0]?.id || "";
    if (!items.length) {
      const empty = node("div", "record-empty");
      empty.append(createIcon(document, query ? "search" : "layers"), node("strong", "", query ? "没有找到匹配项" : "暂无记录"),
        node("p", "", query ? "试试其他名称、房间或别名。" : "刷新列表后，记录会显示在这里。"));
      list.append(empty); editor.hidden = true; return;
    }
    editor.hidden = false;
    const groups = new Map();
    for (const item of items) {
      const group = item.description || "";
      if (!groups.has(group)) groups.set(group, []);
      groups.get(group).push(item);
    }
    for (const [description, records] of groups) {
      if (description) list.append(node("h4", "record-group-title", description));
      for (const item of records) {
        const button = node("button", "record-choice"); button.type = "button";
        button.classList.toggle("is-selected", item.id === selectedId); button.setAttribute("aria-pressed", String(item.id === selectedId));
        const heading = node("span", "record-choice-heading"); heading.append(node("strong", "", item.label));
        if (item.status) heading.append(node("span", `record-availability is-${item.status.state}`, item.status.label));
        button.append(heading);
        if (summaryColumn) {
          const summary = node("span", "record-cell-description"); summaries.set(item.id, summary); updateSummary(item); button.append(summary);
        }
        button.addEventListener("click", () => { if (selectedId === item.id) return; selectedId = item.id; generation += 1; render(); });
        list.append(button);
      }
    }
    const item = items.find(item => item.id === selectedId);
    const identity = node("div", "record-editor-identity");
    const identityText = node("div"); identityText.append(node("h4", "record-editor-title", item.label));
    if (item.description) identityText.append(node("p", "record-editor-subtitle", item.description));
    identity.append(identityText);
    if (item.status) identity.append(node("span", `record-availability is-${item.status.state}`, item.status.label));
    editor.append(identity);
    const fields = node("div", "record-fields"); editor.append(fields);
    for (const column of presentation.columns.filter((column, index) => !column.readonly || index > 0)) {
      const segmented = column.type === "select" && column.options.length <= 4;
      const row = node(segmented ? "fieldset" : "label", `record-field${column.type === "boolean" ? " record-toggle-field" : ""}`);
      row.append(node(segmented ? "legend" : "span", "record-field-label", column.label));
      if (column.readonly) { row.append(node("output", "", item.values?.[column.key] ?? "")); fields.append(row); continue; }
      const change = value => {
        const next = structuredClone(currentValues());
        next[item.id] = { ...(next[item.id] || {}), [column.key]: value };
        write(presentation.valueField, next); updateSummary(item);
      };
      if (segmented) {
        const options = node("div", "record-options");
        const radios = [];
        for (const option of column.options) {
          const label = node("label", "record-option");
          const radio = node("input"); radio.type = "radio"; radio.name = `${scope}-${item.id}-${column.key}`;
          radio.value = String(option.value);
          radio.checked = option.value === (values[item.id]?.[column.key] ?? column.default);
          radio.addEventListener("change", () => { if (radio.checked) change(option.value); });
          label.append(radio, node("span", "", option.label)); options.append(label); radios.push({ radio, value: option.value });
        }
        row.append(options); fields.append(row);
        controls.set(column.key, { update() {
          const value = currentValues()[item.id]?.[column.key] ?? column.default;
          radios.forEach(entry => { entry.radio.checked = entry.value === value; });
        } });
        continue;
      }
      const input = node(column.type === "select" ? "select" : "input");
      input.setAttribute("aria-label", `${item.label} · ${column.label}`);
      if (column.type === "select") {
        for (const option of column.options) { const opt = node("option", "", option.label); opt.value = String(option.value); input.append(opt); }
      } else { input.type = column.type === "boolean" ? "checkbox" : "text"; if (column.maxLength != null) input.maxLength = column.maxLength; }
      const value = values[item.id]?.[column.key] ?? column.default;
      if (column.type === "boolean") input.checked = Boolean(value); else input.value = String(value ?? "");
      input.addEventListener(column.type === "string" ? "input" : "change", () => {
        change(column.type === "boolean" ? input.checked
          : column.type === "select" ? column.options.find(option => String(option.value) === input.value)?.value : input.value);
      });
      row.append(input); fields.append(row);
      controls.set(column.key, { update() {
        if (document.activeElement === input) return;
        const value = currentValues()[item.id]?.[column.key] ?? column.default;
        if (column.type === "boolean") input.checked = Boolean(value); else input.value = String(value ?? "");
        if (column.type === "select") refreshSelect(input);
      } });
    }
    if (presentation.note) editor.append(node("p", "record-table-note", presentation.note));
    if (inspect) {
      const inspection = node("div", "record-inspection");
      const caption = node("strong", "", "当前状态");
      const button = node("button", "secondary-button record-inspect", inspectLabel); button.type = "button";
      button.disabled = pendingId !== null; button.addEventListener("click", () => inspectItem(item, button)); inspection.append(caption, button); editor.append(inspection);
    }
    editor.querySelectorAll("select").forEach(enhanceSelect);
  }
  function update() {
    const items = currentItems();
    const next = JSON.stringify(items);
    if (next !== signature) {
      signature = next; generation += 1; closeDetails(); render(); return;
    }
    for (const control of controls.values()) control.update();
    items.forEach(updateSummary);
  }
  search.addEventListener("input", () => { generation += 1; render(); });
  update();
  return { element: root, update, cancel() { generation += 1; closeSelects(root); },
    dispose() { disposed = true; generation += 1; closeSelects(root); closeDetails(); root.remove(); } };
}
