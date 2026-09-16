export function createAsrAvailability({ invoke, onChange,
  schedule = (callback) => setTimeout(callback, 2000), unschedule = clearTimeout }) {
  let enabled = false, disposed = false, pending = null, timer = null;
  async function refresh() {
    if (disposed) return;
    if (pending) return pending;
    pending = (async () => {
      try {
        const value = await invoke("asr_availability");
        if (disposed || typeof value?.enabled !== "boolean") return;
        if (value.enabled !== enabled) { enabled = value.enabled; onChange(enabled); }
      } catch { /* Keep the last known state during a short generation transition. */ }
      finally { pending = null; }
    })();
    return pending;
  }
  async function tick() {
    await refresh();
    if (!disposed) timer = schedule(tick);
  }
  return { start: tick, refresh, enabled: () => enabled,
    dispose() { disposed = true; unschedule(timer); } };
}
