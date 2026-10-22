import { createIcon } from "./icons.js";
import { createDateInput } from "./date-input.js";

export function formatDate(date) {
  return `${String(date.getFullYear()).padStart(4, "0")}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

function localDate(year, month, day) {
  const date = new Date(0);
  date.setFullYear(year, month, day);
  date.setHours(0, 0, 0, 0);
  return date;
}

export function parseDate(value) {
  const [year, month, day] = value.split("-").map(Number);
  return localDate(year, month - 1, day);
}

export function presetDateRange(days, today = new Date()) {
  const start = localDate(today.getFullYear(), today.getMonth(), today.getDate() - days + 1);
  return { startDate: formatDate(start), endDate: formatDate(today) };
}

export function createDateRangePicker({ document, id, onChange, loadRange, loadDates, isCurrent,
  singleDate = false, getInitialDate }) {
  const win = document.defaultView;
  const element = document.createElement("div");
  element.className = singleDate ? "data-transfer-menu" : "data-transfer-range";
  const trigger = document.createElement("button");
  trigger.type = "button";
  trigger.className = singleDate ? "data-transfer-trigger" : "date-range-trigger";
  trigger.setAttribute("aria-haspopup", "dialog");
  trigger.setAttribute("aria-expanded", "false");
  const label = document.createElement("span");
  label.textContent = "当天";
  trigger.append(createIcon(document, "calendar-days"));
  if (singleDate) {
    trigger.title = "跳转到日期";
    trigger.setAttribute("aria-label", trigger.title);
  } else trigger.append("日期范围", label, createIcon(document, "chevron-down"));
  const panel = document.createElement(singleDate ? "div" : "dialog");
  panel.id = `${id}-dates`;
  panel.className = singleDate ? "date-range-panel date-single-panel" : "date-range-panel";
  if (singleDate) {
    panel.setAttribute("popover", "auto");
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-label", "跳转到日期");
    trigger.popoverTargetElement = panel;
  }
  else panel.setAttribute("aria-labelledby", `${id}-dates-title`);
  panel.tabIndex = -1;
  trigger.setAttribute("aria-controls", panel.id);
  const calendar = `<div class="date-range-calendar">
    <div class="date-range-month"><button type="button" data-prev aria-label="上个月"></button>
      <span data-month aria-live="polite"></span><button type="button" data-next aria-label="下个月"></button></div>
    <div class="date-range-weekdays" aria-hidden="true"><span>日</span><span>一</span><span>二</span><span>三</span><span>四</span><span>五</span><span>六</span></div>
    <div class="date-range-days" role="group" aria-label="日期"></div>
  </div>`;
  panel.innerHTML = singleDate ? `${calendar}<p class="date-range-error" role="status"></p>`
    : `<h2 id="${id}-dates-title" tabindex="-1" autofocus>选择日期范围</h2>
    <div class="date-range-presets" role="group" aria-label="快捷日期"></div>
    <div class="date-range-layout">
      <div class="date-range-fields">
        <p class="date-range-hint">范围包含起止当天。</p>
        <div class="date-range-field" data-field="startDate" role="group" aria-label="开始日期">开始日期</div>
        <div class="date-range-field" data-field="endDate" role="group" aria-label="结束日期">结束日期</div>
        <p class="date-range-error" role="status"></p>
        <div class="date-range-actions"><button type="button" data-cancel>取消</button><button type="button" data-apply>确定</button></div>
      </div>
      ${calendar}
    </div>`;
  panel.querySelector("[data-prev]").append(createIcon(document, "chevron-right"));
  panel.querySelector("[data-next]").append(createIcon(document, "chevron-right"));
  element.append(trigger, panel);
  const inputs = {};
  const error = panel.querySelector(".date-range-error");
  const grid = panel.querySelector(".date-range-days");
  let applied = presetDateRange(1);
  let selectedDate;
  let activeField = "startDate";
  let month;
  let monthAnimation;
  let preset = 1;
  let draftPreset = 1;
  let revision = 0;
  const isOpen = () => singleDate ? panel.matches(":popover-open") : panel.open;
  const closePanel = () => singleDate ? panel.hidePopover() : panel.close();

  function positionPanel() {
    const bounds = trigger.getBoundingClientRect();
    panel.style.left = `${Math.max(8, Math.min(bounds.right - panel.offsetWidth, win.innerWidth - panel.offsetWidth - 8))}px`;
    panel.style.top = `${Math.max(8, Math.min(bounds.bottom + 8, win.innerHeight - panel.offsetHeight - 8))}px`;
  }

  for (const field of panel.querySelectorAll("[data-field]")) {
    const name = field.dataset.field;
    inputs[name] = createDateInput(field, () => {
      revision++;
      panel.querySelector("[data-apply]").disabled = false;
      draftPreset = null;
      error.textContent = "";
      const day = parseDate(inputs[name].value);
      if (day.getMonth() !== month.getMonth() || day.getFullYear() !== month.getFullYear()) {
        const direction = Math.sign(day - month);
        month = localDate(day.getFullYear(), day.getMonth(), 1);
        renderMonth(undefined, direction);
      } else updateSelection();
    });
    field.addEventListener("input", () => {
      revision++;
      panel.querySelector("[data-apply]").disabled = false;
    });
    field.addEventListener("focusin", () => {
      activeField = name;
      updateSelection();
    });
  }

  function updateSelection() {
    for (const button of panel.querySelectorAll("[data-days]")) {
      button.setAttribute("aria-pressed", String(draftPreset !== null && Number(button.dataset.days) === draftPreset));
    }
    for (const field of panel.querySelectorAll("[data-field]")) {
      field.classList.toggle("is-active", field.dataset.field === activeField);
    }
    for (const button of grid.children) {
      const date = button.dataset.date;
      const endpoint = singleDate ? date === selectedDate
        : date === inputs.startDate.value || date === inputs.endDate.value;
      button.setAttribute("aria-pressed", String(endpoint));
      button.classList.toggle("is-in-range", !singleDate && date >= inputs.startDate.value && date <= inputs.endDate.value);
    }
  }

  function renderMonth(focusDate, direction = 0) {
    monthAnimation?.cancel();
    panel.querySelector("[data-month]").textContent = `${month.getFullYear()}年${month.getMonth() + 1}月`;
    const first = localDate(month.getFullYear(), month.getMonth(), 1);
    const daysInMonth = localDate(month.getFullYear(), month.getMonth() + 1, 0).getDate();
    const cellCount = Math.ceil((first.getDay() + daysInMonth) / 7) * 7;
    const today = formatDate(new Date());
    const tabDate = focusDate || (singleDate ? selectedDate : inputs[activeField].value);
    const buttons = [];
    for (let i = 0; i < cellCount; i++) {
      const day = localDate(first.getFullYear(), first.getMonth(), 1 - first.getDay() + i);
      const date = formatDate(day);
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = day.getDate();
      button.dataset.date = date;
      button.setAttribute("aria-label", `${day.getFullYear()}年${day.getMonth() + 1}月${day.getDate()}日`);
      if (date === today) button.setAttribute("aria-current", "date");
      button.classList.toggle("is-outside-month", day.getMonth() !== month.getMonth());
      button.tabIndex = date === tabDate ? 0 : -1;
      button.addEventListener("click", () => {
        if (singleDate) {
          apply({ startDate: date, endDate: date }, null);
          return;
        }
        revision++;
        panel.querySelector("[data-apply]").disabled = false;
        draftPreset = null;
        if (activeField === "startDate") {
          inputs.startDate.value = date;
          if (date > inputs.endDate.value) inputs.endDate.value = date;
          activeField = "endDate";
        } else if (date < inputs.startDate.value) {
          inputs.startDate.value = date;
        } else inputs.endDate.value = date;
        error.textContent = "";
        if (day.getMonth() !== month.getMonth()) {
          const direction = day < month ? -1 : 1;
          month = localDate(day.getFullYear(), day.getMonth(), 1);
          renderMonth(date, direction);
          grid.querySelector(`[data-date="${date}"]`).focus();
        } else {
          for (const item of grid.children) item.tabIndex = item === button ? 0 : -1;
        }
        updateSelection();
      });
      button.addEventListener("keydown", event => {
        const offset = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7,
          Home: -day.getDay(), End: 6 - day.getDay() }[event.key];
        if (offset === undefined) return;
        event.preventDefault();
        const next = localDate(day.getFullYear(), day.getMonth(), day.getDate() + offset);
        const value = formatDate(next);
        if (next.getMonth() !== month.getMonth() || next.getFullYear() !== month.getFullYear()) {
          month = localDate(next.getFullYear(), next.getMonth(), 1);
          renderMonth(value, Math.sign(offset));
        }
        for (const item of grid.children) item.tabIndex = item.dataset.date === value ? 0 : -1;
        grid.querySelector(`[data-date="${value}"]`).focus();
      });
      buttons.push(button);
    }
    if (!buttons.some(button => button.tabIndex === 0)) buttons[first.getDay()].tabIndex = 0;
    grid.replaceChildren(...buttons);
    updateSelection();
    if (loadDates) void updateRecordedDates(buttons);
    if (direction && !win.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      monthAnimation = grid.animate([
        { opacity: .3, transform: `translateX(${direction * 12}px)` },
        { opacity: 1, transform: "translateX(0)" },
      ], { duration: 180, easing: "cubic-bezier(.22, 1, .36, 1)" });
    }
  }

  async function updateRecordedDates(buttons) {
    const current = () => isOpen() && isCurrent() && grid.firstElementChild === buttons[0];
    grid.classList.remove("has-record-dates");
    grid.setAttribute("aria-busy", "true");
    error.textContent = "";
    try {
      const dates = new Set(await loadDates(buttons[0].dataset.date, buttons.at(-1).dataset.date));
      if (!current()) return;
      for (const button of buttons) {
        if (!dates.has(button.dataset.date)) continue;
        button.classList.add("has-records");
        button.setAttribute("aria-description", "有聊天记录");
      }
      grid.classList.add("has-record-dates");
    } catch (failure) {
      if (current()) error.textContent = `记录日期读取失败：${failure?.message || failure}`;
    } finally {
      if (current()) grid.removeAttribute("aria-busy");
    }
  }

  function apply(value, days) {
    applied = value;
    preset = days;
    if (!singleDate) label.textContent = days === 1 ? "当天" : days === 0 ? "全部记录"
      : `${value.startDate.replaceAll("-", "/")} 至 ${value.endDate.replaceAll("-", "/")}`;
    onChange();
    closePanel();
  }

  const presets = singleDate ? []
    : [[1, "当天"], [7, "近 7 天"], [14, "近 14 天"], [30, "近 30 天"], [0, "全部"]];
  for (const [days, title] of presets) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = title;
    button.dataset.days = days;
    button.addEventListener("click", async () => {
      const currentRevision = ++revision;
      panel.querySelector("[data-apply]").disabled = days === 0;
      button.setAttribute("aria-busy", String(days === 0));
      error.textContent = "";
      try {
        const range = days === 0 ? await loadRange() : presetDateRange(days);
        if (!panel.open || !isCurrent() || revision !== currentRevision) return;
        const empty = days === 0 && !range.startDate;
        const value = empty ? presetDateRange(1) : range;
        draftPreset = days;
        inputs.startDate.value = value.startDate;
        inputs.endDate.value = value.endDate;
        const start = parseDate(value.startDate);
        month = localDate(start.getFullYear(), start.getMonth(), 1);
        renderMonth();
        error.textContent = empty ? "当前角色暂无记录。" : "";
      } catch (failure) {
        if (panel.open && isCurrent() && revision === currentRevision) error.textContent = String(failure?.message || failure);
      } finally {
        button.removeAttribute("aria-busy");
        if (revision === currentRevision) panel.querySelector("[data-apply]").disabled = false;
      }
    });
    panel.querySelector(".date-range-presets").append(button);
  }
  for (const [selector, offset] of [["[data-prev]", -1], ["[data-next]", 1]]) {
    panel.querySelector(selector).addEventListener("click", () => {
      month = localDate(month.getFullYear(), month.getMonth() + offset, 1);
      renderMonth(undefined, offset);
    });
  }
  if (singleDate) {
    panel.addEventListener("beforetoggle", event => {
      if (event.newState === "closed") onClose();
    });
    win.addEventListener("resize", () => { if (isOpen()) positionPanel(); });
  } else {
    panel.querySelector("[data-cancel]").addEventListener("click", () => panel.close());
    panel.querySelector("[data-apply]").addEventListener("click", () => {
      const startDate = inputs.startDate.value;
      const endDate = inputs.endDate.value;
      if (startDate > endDate) {
        error.textContent = "结束日期不能早于开始日期。";
        inputs.endDate.focus();
        return;
      }
      apply({ startDate, endDate }, draftPreset);
    });
  }
  function onClose() {
    revision++;
    monthAnimation?.cancel();
    trigger.setAttribute("aria-expanded", "false");
    if (isCurrent() && (!singleDate || panel.contains(document.activeElement))) trigger.focus({ preventScroll: true });
  }
  if (!singleDate) panel.addEventListener("close", onClose);
  trigger.addEventListener("click", event => {
    // 此处负责初始化并切换弹层，取消按钮默认的第二次切换。
    if (singleDate) event.preventDefault();
    if (isOpen()) {
      closePanel();
      return;
    }
    revision++;
    const initialDate = getInitialDate?.() || applied.startDate;
    if (singleDate) selectedDate = initialDate;
    else {
      inputs.startDate.value = initialDate;
      inputs.endDate.value = applied.endDate;
      panel.querySelector("[data-apply]").disabled = false;
    }
    activeField = "startDate";
    error.textContent = "";
    draftPreset = preset;
    const start = parseDate(initialDate);
    month = localDate(start.getFullYear(), start.getMonth(), 1);
    renderMonth();
    if (singleDate) {
      panel.showPopover();
      positionPanel();
      grid.querySelector('[tabindex="0"]').focus({ preventScroll: true });
    } else panel.showModal();
    trigger.setAttribute("aria-expanded", "true");
    panel.scrollTop = 0;
  });
  return { element, trigger, getValue: () => ({ ...applied }), dispose() {
    monthAnimation?.cancel();
    revision++;
    if (isOpen()) closePanel();
  } };
}
