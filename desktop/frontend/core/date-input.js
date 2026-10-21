// 分段编辑保留主题选区；对外统一使用 YYYY-MM-DD。
export function createDateInput(field, onChange) {
  const document = field.ownerDocument;
  const label = field.textContent.trim();
  const row = document.createElement("span");
  row.className = "date-input-segments";
  const parts = ["年", "月", "日"].map((name, index) => {
    if (index) row.append("/");
    const input = document.createElement("input");
    input.type = "text";
    input.inputMode = "numeric";
    input.autocomplete = "off";
    input.maxLength = index === 0 ? 4 : 2;
    input.setAttribute("aria-label", `${label}：${name}`);
    row.append(input);
    return input;
  });
  field.append(row);
  let previous;
  let lastValue;
  const maximum = index => {
    if (index < 2) return index === 0 ? 9999 : 12;
    const date = new Date(0);
    date.setFullYear(Number(parts[0].value), Number(parts[1].value), 0);
    return date.getDate();
  };
  const clamp = (value, index) => Math.max(1, Math.min(maximum(index), Number(value)));
  function normalize(index) {
    parts[index].value = String(clamp(parts[index].value || previous, index)).padStart(index === 0 ? 4 : 2, "0");
    parts[2].value = String(clamp(parts[2].value, 2)).padStart(2, "0");
  }
  const control = {
    get value() { return parts.map(part => part.value).join("-"); },
    set value(value) {
      value.split("-").forEach((part, index) => { parts[index].value = part; });
      lastValue = value;
    },
    focus() { parts[0].focus(); },
  };
  function changed() {
    if (control.value === lastValue) return;
    lastValue = control.value;
    onChange();
  }
  parts.forEach((input, index) => {
    input.addEventListener("focus", () => { previous = input.value; input.select(); });
    input.addEventListener("click", () => input.select());
    input.addEventListener("blur", () => {
      normalize(index);
      changed();
    });
    input.addEventListener("input", () => {
      input.value = input.value.replace(/\D/g, "");
      if (!input.value) return;
      const complete = input.value.length === input.maxLength
        || (index === 1 && Number(input.value) * 10 > maximum(index));
      if (!complete) return;
      normalize(index);
      changed();
      if (index < 2) parts[index + 1].focus();
      else input.select();
    });
    input.addEventListener("keydown", event => {
      const offset = event.key === "ArrowLeft" ? -1
        : ["ArrowRight", "/", "-"].includes(event.key) ? 1 : 0;
      if (offset) {
        event.preventDefault();
        parts[Math.max(0, Math.min(2, index + offset))].focus();
      } else if (event.key === "ArrowUp" || event.key === "ArrowDown") {
        event.preventDefault();
        input.value = String(Number(input.value) + (event.key === "ArrowUp" ? 1 : -1));
        normalize(index);
        input.select();
        changed();
      } else if (event.key === "Backspace" && !input.value && index > 0) {
        event.preventDefault();
        parts[index - 1].focus();
      }
    });
    input.addEventListener("paste", event => {
      const match = event.clipboardData.getData("text").trim().match(/^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$/);
      if (!match) return;
      event.preventDefault();
      parts.forEach((part, i) => {
        part.value = String(clamp(match[i + 1], i)).padStart(i === 0 ? 4 : 2, "0");
      });
      input.select();
      changed();
    });
  });
  return control;
}
