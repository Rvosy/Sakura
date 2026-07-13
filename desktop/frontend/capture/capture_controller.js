export class CaptureController {
  constructor({ store, invoke, setStatus = () => {} }) {
    this.store = store;
    this.invoke = invoke;
    this.setStatus = setStatus;
  }

  async open() {
    if (this.store.getState().interaction.busy) return null;
    this.store.setInteractionState({ busy: true, interactionId: null });
    this.setStatus("拖动鼠标框选要附加的屏幕区域。", "ready");
    try {
      return await this.invoke("open_capture_overlay", {});
    } catch (error) {
      this.store.setInteractionState({ busy: false, interactionId: null });
      this.setStatus(`截图启动失败：${error}`, "error");
      throw error;
    }
  }

  handleReady(payload) {
    const observationId = String(payload?.observationId || "").trim();
    if (!observationId) return;
    this.store.setObservationState({
      attached: true,
      observationId,
      width: Number(payload?.width || 0),
      height: Number(payload?.height || 0),
    });
    this.store.setInteractionState({ busy: false, interactionId: null });
    this.setStatus("截图已附加，发送消息后会立即从内存中释放。", "success");
  }

  handleCancelled() {
    this.store.setInteractionState({ busy: false, interactionId: null });
    this.setStatus("已取消截图。", "ready");
  }

  handleError(payload) {
    this.store.setInteractionState({ busy: false, interactionId: null });
    this.setStatus(payload?.message || "截图失败，请重试。", "error");
  }

  attachment() {
    const current = this.store.getState().observation;
    if (!current?.attached || !current.observationId) return null;
    return {
      observationId: current.observationId,
      width: current.width,
      height: current.height,
    };
  }

  clearAttachment() {
    this.store.clearObservation();
  }

  consumeAttachment() {
    const attachment = this.attachment();
    if (attachment) this.clearAttachment();
    return attachment;
  }
}
