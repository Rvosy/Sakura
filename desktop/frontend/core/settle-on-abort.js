export function settleOnAbort(promise, signal) {
  let onAbort;
  const cancelled = new Promise(resolve => {
    onAbort = () => resolve(undefined);
    signal.addEventListener("abort", onAbort, { once: true });
    if (signal.aborted) onAbort();
  });
  return Promise.race([promise, cancelled]).finally(() => signal.removeEventListener("abort", onAbort));
}
