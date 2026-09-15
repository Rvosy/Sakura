export async function attachDynamicHitTest({ invoke, listen, container, hitTest, signal, onError = () => {} }) {
  const session = crypto.randomUUID();
  let stopped = false;
  const unlisten = await listen('visual-hit-test', async ({ payload }) => {
    if (stopped || signal.aborted || payload.session !== session) return;
    try {
      const rect = container.getBoundingClientRect();
      const point = [(payload.point[0] - rect.left) / rect.width, (payload.point[1] - rect.top) / rect.height];
      const hit = await hitTest(point);
      if (stopped || signal.aborted) return;
      await invoke('submit_dynamic_hit_test', { session, id: payload.id, hit: Boolean(hit) });
    } catch (error) { stop(); onError(error); }
  });
  function stop() {
    if (stopped) return;
    stopped = true;
    unlisten();
    signal.removeEventListener('abort', stop);
    void invoke('configure_dynamic_hit_test', { session, enabled: false }).catch(onError);
  }
  if (signal.aborted) { stop(); return false; }
  signal.addEventListener('abort', stop, { once: true });
  try {
    const enabled = await invoke('configure_dynamic_hit_test', { session, enabled: true });
    // If activation completed after cleanup, retire that exact session again.
    // A replacement renderer's different session must remain active.
    if (stopped || signal.aborted) await invoke('configure_dynamic_hit_test', { session, enabled: false });
    if (signal.aborted || !enabled) stop();
    return enabled && !signal.aborted;
  } catch (error) { stop(); onError(error); return false; }
}
