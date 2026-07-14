function cloneLayout(layout) {
  return { ...(layout || {}) };
}

function createSessionId() {
  const randomUuid = globalThis.crypto?.randomUUID?.();
  if (randomUuid) return `settings-${randomUuid}`;
  return `settings-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export class LayoutPreviewTransaction {
  constructor({
    invoke,
    scheduleFrame = (callback) => window.requestAnimationFrame(callback),
    sessionId = createSessionId(),
  }) {
    this.invoke = invoke;
    this.scheduleFrame = scheduleFrame;
    this.sessionId = sessionId;
    this.revision = 0;
    this.lastSentRevision = 0;
    this.latestLayout = null;
    this.baselineLayout = null;
    this.frameScheduled = false;
    this.started = false;
    this.closed = false;
  }

  async begin(layout) {
    const baseline = cloneLayout(layout);
    await this.invoke("begin_layout_preview", {
      sessionId: this.sessionId,
      revision: this.revision,
      layout: baseline,
    });
    this.baselineLayout = baseline;
    this.latestLayout = cloneLayout(baseline);
    this.started = true;
  }

  preview(layout) {
    if (!this.started || this.closed) return this.revision;
    this.latestLayout = cloneLayout(layout);
    this.revision += 1;
    if (!this.frameScheduled) {
      this.frameScheduled = true;
      this.scheduleFrame(() => {
        this.frameScheduled = false;
        this.flushPreview().catch(() => {});
      });
    }
    return this.revision;
  }

  async flushPreview() {
    if (!this.started || this.closed || this.revision <= this.lastSentRevision) return;
    const revision = this.revision;
    const layout = cloneLayout(this.latestLayout);
    this.lastSentRevision = revision;
    await this.invoke("preview_layout", {
      sessionId: this.sessionId,
      revision,
      layout,
    });
    if (!this.closed && this.revision > this.lastSentRevision && !this.frameScheduled) {
      this.frameScheduled = true;
      this.scheduleFrame(() => {
        this.frameScheduled = false;
        this.flushPreview().catch(() => {});
      });
    }
  }

  async apply(settings, runtimeLayout) {
    const response = await this.invoke("apply_settings", {
      settings,
      previewSessionId: this.sessionId,
      previewRevision: this.revision,
    });
    this.baselineLayout = cloneLayout(runtimeLayout);
    return response;
  }

  async save(settings, runtimeLayout) {
    const response = await this.invoke("save_settings", {
      settings,
      previewSessionId: this.sessionId,
      previewRevision: this.revision,
    });
    this.baselineLayout = cloneLayout(runtimeLayout);
    this.closed = true;
    return response;
  }

  async cancel() {
    if (this.closed) return;
    this.closed = true;
    try {
      await this.invoke("cancel_settings", { previewSessionId: this.sessionId });
    } catch (error) {
      this.closed = false;
      throw error;
    }
  }
}
