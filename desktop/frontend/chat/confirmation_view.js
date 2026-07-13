export class ConfirmationView {
  constructor({ panel, name, reason, argumentsView, confirmButton, rejectButton, onConfirm, onReject }) {
    this.panel = panel;
    this.name = name;
    this.reason = reason;
    this.argumentsView = argumentsView;
    this.confirmButton = confirmButton;
    this.rejectButton = rejectButton;
    this.onConfirm = onConfirm;
    this.onReject = onReject;
    this.actionId = null;
    confirmButton.addEventListener("click", () => {
      if (this.actionId) this.onConfirm(this.actionId);
    });
    rejectButton.addEventListener("click", () => {
      if (this.actionId) this.onReject(this.actionId);
    });
  }

  show(action) {
    const id = String(action?.id || "").trim();
    if (!id) return this.hide();
    this.actionId = id;
    this.name.textContent = String(action.toolName || "未知工具");
    this.reason.textContent = String(action.reason || "该操作需要你的确认。");
    this.argumentsView.textContent = JSON.stringify(action.arguments || {}, null, 2);
    this.panel.hidden = false;
    this.setBusy(false);
  }

  hide() {
    this.actionId = null;
    this.panel.hidden = true;
    this.name.textContent = "";
    this.reason.textContent = "";
    this.argumentsView.textContent = "";
    this.setBusy(false);
  }

  setBusy(busy) {
    this.confirmButton.disabled = Boolean(busy);
    this.rejectButton.disabled = Boolean(busy);
  }
}
