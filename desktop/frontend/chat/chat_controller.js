function interactionId(payload) {
  return String(payload?.interactionId || "").trim();
}

function errorMessage(error) {
  if (error && typeof error === "object" && typeof error.message === "string") {
    return error.message;
  }
  return String(error || "聊天请求失败，请稍后重试。");
}

export class ChatController {
  constructor({
    store,
    invoke,
    subtitleController,
    confirmationView,
    audioController = null,
    setStatus = () => {},
  }) {
    this.store = store;
    this.invoke = invoke;
    this.subtitleController = subtitleController;
    this.confirmationView = confirmationView;
    this.audioController = audioController;
    this.setStatus = setStatus;
    this.submitting = false;
  }

  async send(text) {
    const message = String(text || "").trim();
    if (!message || this.store.getState().interaction.busy) return null;
    this.confirmationView.hide();
    this.submitting = true;
    this.store.setInteractionState({ busy: true, interactionId: null });
    this.subtitleController.setText("……");
    this.setStatus("正在等待角色回复…", "ready");
    try {
      const accepted = await this.invoke("chat_send", { text: message });
      this.#acceptInteraction(accepted);
      return accepted;
    } catch (error) {
      this.#handleInvokeError(error);
      throw error;
    } finally {
      this.submitting = false;
    }
  }

  async cancel() {
    const current = this.store.getState().interaction;
    if (!current.busy || !current.interactionId) return null;
    this.setStatus("正在取消当前回复…", "ready");
    try {
      return await this.invoke("chat_cancel", { interactionId: current.interactionId });
    } catch (error) {
      this.#handleInvokeError(error);
      throw error;
    }
  }

  async confirm(actionId) {
    return this.#resolveAction("chat_confirm_action", actionId, "正在执行已确认操作…");
  }

  async reject(actionId) {
    return this.#resolveAction("chat_reject_action", actionId, "正在生成取消后的回复…");
  }

  handleProgress(payload) {
    if (!this.#matches(payload)) return;
    this.audioController?.stop().catch(() => {});
    const segments = payload?.reply?.segments || [];
    if (segments.length) this.subtitleController.showSegments(segments);
    if (segments.length) this.audioController?.queueSegments(segments);
    this.setStatus(`处理中 · ${payload?.stage || "thinking"}`, "ready");
  }

  handleReply(payload) {
    if (!this.#matches(payload)) return;
    const segments = payload?.reply?.segments || [];
    this.store.setInteractionState({ busy: false });
    this.confirmationView.setBusy(false);
    if (segments.length) this.subtitleController.showSegments(segments);
    const pending = Array.isArray(payload?.pendingActions) ? payload.pendingActions : [];
    if (pending[0]) this.confirmationView.show(pending[0]);
    this.setStatus("角色回复完成。", "success");
  }

  handleConfirmation(payload) {
    if (!this.#matches(payload) || !payload?.action?.id) return;
    this.confirmationView.show(payload.action);
  }

  handleCancelled(payload) {
    if (!this.#matches(payload)) return;
    this.store.setInteractionState({ busy: false });
    this.confirmationView.setBusy(false);
    this.subtitleController.cancel("已取消当前回复。");
    this.audioController?.stop().catch(() => {});
    this.setStatus("当前回复已取消。", "ready");
  }

  handleError(payload) {
    if (!this.#matches(payload)) return;
    this.store.setInteractionState({ busy: false });
    this.confirmationView.setBusy(false);
    const message = errorMessage(payload?.error);
    this.subtitleController.cancel("……通信に失敗した。設定を確認して。");
    this.audioController?.stop().catch(() => {});
    this.setStatus(message, "error");
  }

  reset() {
    this.submitting = false;
    this.confirmationView.hide();
    this.subtitleController.cancel("");
    this.audioController?.stop().catch(() => {});
  }

  async #resolveAction(command, actionId, status) {
    const id = String(actionId || "").trim();
    if (!id || this.store.getState().interaction.busy) return null;
    this.confirmationView.setBusy(true);
    this.store.setInteractionState({ busy: true, interactionId: null });
    this.setStatus(status, "ready");
    try {
      const accepted = await this.invoke(command, { actionId: id });
      this.#acceptInteraction(accepted);
      return accepted;
    } catch (error) {
      this.confirmationView.setBusy(false);
      this.#handleInvokeError(error);
      throw error;
    }
  }

  #acceptInteraction(accepted) {
    const id = interactionId(accepted);
    if (!id || !this.store.getState().interaction.busy) return;
    const current = this.store.getState().interaction.interactionId;
    if (!current || current === id) this.store.setInteractionState({ interactionId: id });
  }

  #matches(payload) {
    const id = interactionId(payload);
    if (!id) return false;
    const current = this.store.getState().interaction;
    if (current.interactionId === id) return true;
    if (!current.interactionId && current.busy) {
      this.store.setInteractionState({ interactionId: id });
      return true;
    }
    return false;
  }

  #handleInvokeError(error) {
    this.store.setInteractionState({ busy: false });
    this.confirmationView.setBusy(false);
    const message = errorMessage(error);
    this.subtitleController.cancel("……通信に失敗した。設定を確認して。");
    this.setStatus(message, "error");
  }
}
