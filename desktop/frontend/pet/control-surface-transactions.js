// A new gesture may supersede queued work, but an already applied native frame must still
// reach the DOM before the next transaction changes it again.
export function createControlSurfaceTransactions({ isCurrent, isDisposed, commit }) {
  let pending = Promise.resolve();
  return (revision, apply) => {
    const transaction = pending.then(async () => {
      if (isDisposed() || !isCurrent(revision)) return null;
      const surface = await apply();
      if (surface && !isDisposed()) commit(surface);
      return surface;
    });
    pending = transaction.catch(() => {});
    return transaction;
  };
}
