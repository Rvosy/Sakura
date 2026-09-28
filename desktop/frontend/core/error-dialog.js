import { errorText } from "./error-display.js";

let nextDialogId = 0;

export function createErrorDialog({ document, onOpen = () => {}, onClose = () => {} }) {
  let elements = null;
  let disposed = false;

  function ensureDialog() {
    if (elements) return elements;
    const id = `sakura-error-${++nextDialogId}`;
    const dialog = document.createElement("dialog");
    dialog.className = "sakura-error-dialog";
    dialog.dataset.interactive = "true";
    dialog.setAttribute("aria-labelledby", `${id}-title`);
    const title = document.createElement("h2");
    title.id = `${id}-title`;
    const message = document.createElement("p");
    message.id = `${id}-message`;
    message.className = "sakura-error-message";
    const details = document.createElement("details");
    details.className = "sakura-error-details";
    const summary = document.createElement("summary");
    summary.textContent = "错误详情";
    const diagnostic = document.createElement("pre");
    diagnostic.tabIndex = 0;
    diagnostic.setAttribute("aria-label", "错误详情");
    diagnostic.dataset.selectableText = "true";
    details.append(summary, diagnostic);
    const actions = document.createElement("div");
    actions.className = "sakura-error-actions";
    const close = document.createElement("button");
    close.type = "button";
    close.autofocus = true;
    close.textContent = "关闭";
    close.addEventListener("click", () => dialog.close());
    actions.append(close);
    dialog.append(title, message, details, actions);
    dialog.addEventListener("close", onClose);
    dialog.addEventListener("click", event => {
      if (event.target !== dialog) return;
      const bounds = dialog.getBoundingClientRect();
      if (event.clientX < bounds.left || event.clientX > bounds.right
          || event.clientY < bounds.top || event.clientY > bounds.bottom) dialog.close();
    });
    document.body.append(dialog);
    elements = { dialog, title, message, details, diagnostic };
    return elements;
  }

  return Object.freeze({
    show({ title = "操作失败", message = "", error } = {}) {
      if (disposed) return null;
      const ui = ensureDialog();
      const diagnostic = errorText(error ?? (message || title));
      if (ui.title.textContent !== title || ui.diagnostic.textContent !== diagnostic) {
        ui.details.open = !message;
      }
      ui.title.textContent = title;
      ui.message.textContent = message;
      ui.message.hidden = !message;
      if (message) ui.dialog.setAttribute("aria-describedby", ui.message.id);
      else ui.dialog.removeAttribute("aria-describedby");
      ui.diagnostic.textContent = diagnostic;
      if (!ui.dialog.open) {
        ui.dialog.showModal();
        onOpen(ui.dialog);
      }
      return ui.dialog;
    },
    close() { if (elements?.dialog.open) elements.dialog.close(); },
    get isOpen() { return Boolean(elements?.dialog.open); },
    dispose() {
      disposed = true;
      if (!elements) return;
      if (elements.dialog.open) elements.dialog.close();
      elements.dialog.remove();
      elements = null;
    },
  });
}
