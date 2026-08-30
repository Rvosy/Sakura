const CTRL_SHIFT_DEVTOOLS_KEYS = new Set(["c", "i", "j"]);

export function isDevtoolsShortcut(event) {
  const key = String(event?.key || "").toLowerCase();
  return event?.key === "F12"
    || Boolean(event?.ctrlKey && event?.shiftKey && CTRL_SHIFT_DEVTOOLS_KEYS.has(key))
    || Boolean(event?.metaKey && event?.altKey && key === "i");
}

export function suppressDevtoolsShortcut(event) {
  if (!isDevtoolsShortcut(event)) return false;
  event.preventDefault();
  event.stopImmediatePropagation();
  return true;
}

export function installDevtoolsShortcutGuard(target = window) {
  target.addEventListener("keydown", suppressDevtoolsShortcut, { capture: true });
}
