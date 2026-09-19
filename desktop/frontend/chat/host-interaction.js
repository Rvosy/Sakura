// Publish desktop activity facts. Scheduling decisions belong to feature plugins.
export function createHostInteractionController({ invoke, generationId, isReady, isIdle, onDiagnostic = () => {},
  intervalMs = 200, schedule = (fn, ms) => window.setInterval(fn, ms), unschedule = id => window.clearInterval(id) }) {
  let session = null, generation = null, revision = 0, epoch = 0, last = null, busy = false, timer = null, disposed = false;
  function invalidate() { session = null; generation = null; last = null; epoch += 1; revision += 1; }
  async function update() {
    if (disposed || busy || !isReady()) return;
    const targetGeneration = generationId();
    if (!targetGeneration) return;
    if (generation && generation !== targetGeneration) invalidate();
    const attempt = epoch;
    busy = true;
    try {
      if (!session) {
        const current = await invoke("host_interaction_current", { generationId: targetGeneration });
        if (disposed || attempt !== epoch || generationId() !== targetGeneration) return;
        session = current?.sessionId || null;
        generation = targetGeneration;
        if (!session) return;
      }
      const idle = Boolean(isIdle());
      const signature = JSON.stringify([session, idle, revision]);
      if (signature === last) return;
      const result = await invoke("host_interaction_state", { payload: {
        generationId: targetGeneration, sessionId: session, idle, activityRevision: revision,
      } });
      if (disposed || attempt !== epoch || generationId() !== targetGeneration) return;
      if (result?.accepted) last = signature;
      else invalidate();
    } catch (error) {
      onDiagnostic("host.interaction.failed", { code: String(error).split(":")[0] });
    } finally { busy = false; }
  }
  return Object.freeze({
    start() { if (!disposed && timer === null) { timer = schedule(() => { void update(); }, intervalMs); void update(); } },
    noteActivity() { revision += 1; void update(); },
    invalidate,
    update,
    dispose() { disposed = true; epoch += 1; if (timer !== null) unschedule(timer); timer = null; },
  });
}

export function createHostVisualController({ invoke, renderer, generationId, characterId, isIdle, onError = () => {} }) {
  let active = null;
  let shown = null;
  const matches = target => target?.characterId === characterId()
    && target.bindingId === renderer.current()?.bindingId && target.resourceId === renderer.current()?.resourceId;
  return Object.freeze({
    async receive(event) {
      if (!event || event.generationId !== generationId()) return;
      if (event.type === "host.visual.cancel") {
        if (active?.requestId === event.requestId) {
          active.cancelled = true;
          active = null;
        }
        if (shown?.requestId === event.requestId) {
          renderer.cancelOperation(event.requestId, "plugin_scope_closed");
          shown = null;
        }
        return;
      }
      if (event.type !== "host.visual.apply") return;
      const payload = { generationId: event.generationId, requestId: event.requestId, target: event.target };
      const current = { requestId: event.requestId, playing: false, cancelled: false };
      let displayed = false;
      try {
        if (matches(event.target) && isIdle() && active === null) {
          active = current;
          const claim = await invoke("host_visual_claim", { payload });
          if (claim?.accepted && !current.cancelled && active === current
              && event.generationId === generationId() && matches(event.target) && isIdle()) {
            current.playing = true;
            renderer.begin(event.requestId);
            shown = current;
            displayed = await renderer.play(event.control, event.requestId, 0);
            if (active !== current || current.cancelled) displayed = false;
          }
        }
        await invoke("host_visual_result", { payload: { ...payload, status: displayed ? "displayed" : "failed",
          ...(displayed ? {} : { errorCode: "VISUAL_CONTROL_NOT_DISPLAYED" }) } });
      } catch (error) { onError(error); }
      finally { if (active === current) active = null; }
    },
    busy: () => Boolean(active?.playing),
  });
}
