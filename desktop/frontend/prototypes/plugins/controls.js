/* Mirrors settings/settings.js: native values + themed menus, and deferred dialog exit. */
(() => {
  "use strict";
  const selects = new WeakMap();
  let activeSelect = null;
  let nextId = 0;

  function closeSelects(root = document) {
    if (activeSelect && root.contains(activeSelect.select)) activeSelect.close();
  }

  function enhanceSelect(select) {
    if (selects.has(select)) return;
    const wrapper = document.createElement("span");
    wrapper.className = "custom-select";
    const trigger = document.createElement("button");
    trigger.type = "button";
    trigger.className = "custom-select__trigger";
    trigger.setAttribute("role", "combobox");
    trigger.setAttribute("aria-haspopup", "listbox");
    trigger.setAttribute("aria-expanded", "false");
    const name = select.getAttribute("aria-label") || [...select.labels].map((label) =>
      label.querySelector(":scope > span")?.textContent || label.textContent).join(" ").trim();
    trigger.setAttribute("aria-label", name);
    const label = document.createElement("span");
    label.className = "custom-select__label";
    const caret = document.createElement("span");
    caret.className = "custom-select__caret";
    caret.setAttribute("aria-hidden", "true");
    trigger.append(label, caret);
    const menu = document.createElement("div");
    menu.id = `demo-select-menu-${++nextId}`;
    menu.className = "custom-select__menu";
    menu.setAttribute("role", "listbox");
    menu.setAttribute("aria-label", name);
    menu.setAttribute("popover", "manual");
    trigger.setAttribute("aria-controls", menu.id);
    select.before(wrapper);
    wrapper.append(trigger, select);
    select.tabIndex = -1;
    let current = -1;
    let prefix = "";
    let typedAt = 0;

    function refresh() {
      label.textContent = select.selectedOptions[0]?.textContent || "";
      trigger.disabled = select.disabled;
      if (select.disabled) close();
    }
    function close() {
      wrapper.classList.remove("is-open");
      trigger.setAttribute("aria-expanded", "false");
      trigger.removeAttribute("aria-activedescendant");
      if (menu.matches(":popover-open")) menu.hidePopover();
      menu.remove();
      if (activeSelect?.select === select) activeSelect = null;
    }
    function highlight(index) {
      current = index;
      [...menu.children].forEach((item, i) => item.classList.toggle("is-active", i === index));
      const item = menu.children[index];
      if (item) {
        trigger.setAttribute("aria-activedescendant", item.id);
        item.scrollIntoView({ block: "nearest" });
      }
    }
    function choose(index) {
      if (!select.options[index] || select.options[index].disabled) return;
      const changed = select.selectedIndex !== index;
      select.selectedIndex = index;
      close();
      refresh();
      trigger.focus({ preventScroll: true });
      if (changed) select.dispatchEvent(new Event("change", { bubbles: true }));
    }
    function open() {
      if (select.disabled) return;
      closeSelects();
      menu.replaceChildren(...[...select.options].map((option, index) => {
        const item = document.createElement("div");
        item.id = `${menu.id}-${index}`;
        item.className = "custom-select__option";
        item.setAttribute("role", "option");
        item.setAttribute("aria-selected", String(index === select.selectedIndex));
        item.setAttribute("aria-disabled", String(option.disabled));
        item.textContent = option.textContent;
        item.addEventListener("pointerdown", (event) => event.preventDefault());
        item.addEventListener("click", (event) => { event.stopPropagation(); choose(index); });
        item.addEventListener("pointermove", () => { if (!option.disabled) highlight(index); });
        return item;
      }));
      // Stay inside a modal's focus scope; the popover top layer avoids scroll/transform clipping.
      (select.closest("dialog") || document.body).append(menu);
      menu.showPopover();
      const rect = trigger.getBoundingClientRect();
      const below = window.innerHeight - rect.bottom - 14;
      const above = rect.top - 14;
      const upwards = below < Math.min(menu.scrollHeight, 260) && above > below;
      menu.style.maxHeight = `${Math.max(40, Math.min(260, upwards ? above : below))}px`;
      menu.style.minWidth = `${Math.min(rect.width, window.innerWidth - 16)}px`;
      menu.style.maxWidth = `${window.innerWidth - 16}px`;
      menu.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - menu.offsetWidth - 8))}px`;
      menu.style.top = `${upwards ? Math.max(8, rect.top - menu.offsetHeight - 6) : rect.bottom + 6}px`;
      wrapper.classList.add("is-open");
      trigger.setAttribute("aria-expanded", "true");
      activeSelect = { select, wrapper, menu, close };
      highlight(select.selectedIndex);
    }
    function move(key) {
      const enabled = [...select.options].map((option, i) => option.disabled ? -1 : i).filter((i) => i >= 0);
      if (!enabled.length) return;
      const index = enabled.indexOf(current);
      highlight(key === "Home" ? enabled[0] : key === "End" ? enabled.at(-1)
        : enabled[(index + (key === "ArrowDown" ? 1 : -1) + enabled.length) % enabled.length]);
    }
    trigger.addEventListener("click", (event) => {
      event.preventDefault();
      activeSelect?.select === select ? close() : open();
    });
    trigger.addEventListener("keydown", (event) => {
      const isOpen = activeSelect?.select === select;
      if (event.key === "Tab") { close(); return; }
      if (event.key === "Escape" && isOpen) {
        event.preventDefault(); event.stopPropagation(); close(); return;
      }
      if (["ArrowDown", "ArrowUp", "Home", "End", "Enter", " "].includes(event.key)) {
        event.preventDefault(); event.stopPropagation();
        if (!isOpen) { open(); if (["Home", "End"].includes(event.key)) move(event.key); }
        else if (["Enter", " "].includes(event.key)) choose(current);
        else move(event.key);
      } else if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
        event.preventDefault(); event.stopPropagation();
        if (!isOpen) open();
        const now = Date.now();
        prefix = (now - typedAt < 700 ? prefix : "") + event.key.toLocaleLowerCase();
        typedAt = now;
        const index = [...select.options].findIndex((option) => !option.disabled && option.textContent.trim().toLocaleLowerCase().startsWith(prefix));
        if (index >= 0) highlight(index);
      }
    });
    [...select.labels].forEach((owner) => owner.addEventListener("click", (event) => {
      if (!wrapper.contains(event.target)) { event.preventDefault(); trigger.focus(); }
    }));
    select.addEventListener("change", refresh);
    selects.set(select, { refresh, trigger });
    refresh();
  }

  document.addEventListener("pointerdown", (event) => {
    if (activeSelect && !activeSelect.wrapper.contains(event.target) && !activeSelect.menu.contains(event.target)) closeSelects();
  }, true);
  window.addEventListener("scroll", (event) => {
    if (activeSelect && !activeSelect.menu.contains(event.target)) closeSelects();
  }, true);
  window.addEventListener("resize", () => closeSelects());

  async function closeDialog(dialog, value = "cancel") {
    if (!dialog.open || dialog.classList.contains("is-closing")) return;
    closeSelects(dialog);
    dialog.classList.add("is-closing");
    await Promise.allSettled(dialog.getAnimations().map((animation) => animation.finished));
    dialog.close(value);
    dialog.classList.remove("is-closing");
  }
  document.querySelectorAll("dialog").forEach((dialog) => {
    dialog.addEventListener("cancel", (event) => { event.preventDefault(); closeDialog(dialog); });
    dialog.addEventListener("close", () => closeSelects(dialog));
  });
  window.PluginDemoControls = {
    enhanceSelects: (root = document) => root.querySelectorAll("select").forEach(enhanceSelect),
    refreshSelect: (select) => selects.get(select)?.refresh(),
    focusSelect: (select) => (selects.get(select)?.trigger || select)?.focus({ preventScroll: true }),
    closeSelects,
    closeDialog,
  };
})();
