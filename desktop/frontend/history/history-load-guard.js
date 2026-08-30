export function createHistoryLoadGuard() {
  let revision = 0;
  return Object.freeze({
    begin: () => revision,
    isCurrent: (candidate) => candidate === revision,
    invalidate() {
      revision += 1;
      return revision;
    },
  });
}

export function historyRefreshAction(payload) {
  if (payload?.reset === true) {
    return Object.freeze({ reset: true, reload: payload.ready === true });
  }
  return Object.freeze({ reset: false, reload: true });
}

export async function subscribeHistoryRefresh(listen, onRefresh) {
  if (typeof listen !== "function") return false;
  try {
    await listen("sakura://history-refresh-requested", onRefresh);
    return true;
  } catch {
    return false;
  }
}
