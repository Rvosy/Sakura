export function createLayoutController({
  computeLayout,
  applyNativeLayout,
  previewLayout = null,
  commitLayout,
  initialRevision = 0,
}) {
  if (!Number.isSafeInteger(initialRevision) || initialRevision < 0) {
    throw new Error("initial layout revision must be a non-negative safe integer");
  }
  let requestedRevision = initialRevision;
  let currentState = null;
  let committedLayout = null;
  let nativeRunning = false;
  let pendingNative = null;

  function rejectedResult({ revision, state }) {
    return Object.freeze({ applied: false, revision, state });
  }

  function restoreCommittedPreview(work) {
    if (
      work.previewed
      && work.revision === requestedRevision
      && work.state === currentState
      && committedLayout
      && previewLayout
    ) {
      previewLayout(committedLayout, { rollback: true, revision: work.revision, state: work.state });
    }
  }

  async function applyPendingNative(work) {
    let nativeResult;
    try {
      nativeResult = await applyNativeLayout({
        state: work.state,
        revision: work.revision,
        layout: work.layout,
      });
    } catch (error) {
      restoreCommittedPreview(work);
      work.reject(error);
      return;
    }

    if (!nativeResult.applied) {
      restoreCommittedPreview(work);
      work.resolve(rejectedResult(work));
      return;
    }
    if (nativeResult.contractVersion !== work.layout.contractVersion) {
      restoreCommittedPreview(work);
      work.reject(new Error("Rust and WebView layout contracts do not match"));
      return;
    }
    if (work.state !== currentState) {
      work.resolve(rejectedResult(work));
      return;
    }

    try {
      const isCurrent = work.revision === requestedRevision;
      if (work.previewed && !isCurrent) {
        work.resolve(rejectedResult(work));
        return;
      }
      // Ordinary adaptive changes remain paired with their precise Win32 clip. During an explicit
      // settings preview the native region is already relaxed, so stale native acknowledgements
      // must not paint over the newest immediate WebView frame.
      commitLayout(work.layout, nativeResult);
      committedLayout = work.layout;
      work.resolve(isCurrent
        ? Object.freeze({
          applied: true,
          revision: work.revision,
          state: work.state,
          nativeResult,
        })
        : rejectedResult(work));
    } catch (error) {
      work.reject(error);
    }
  }

  async function drainNativeQueue() {
    if (nativeRunning) return;
    nativeRunning = true;
    while (pendingNative) {
      const work = pendingNative;
      pendingNative = null;
      await applyPendingNative(work);
    }
    nativeRunning = false;
  }

  function enqueueNative(work) {
    return new Promise((resolve, reject) => {
      if (pendingNative) pendingNative.resolve(rejectedResult(pendingNative));
      pendingNative = { ...work, resolve, reject };
      void drainNativeQueue();
    });
  }

  return Object.freeze({
    async transition(state, placeholderText = "", input = undefined) {
      const revision = ++requestedRevision;
      currentState = state;
      const layout = computeLayout(state, placeholderText, input);
      const previewed = Boolean(input?.visualPreview && previewLayout);
      if (previewed) previewLayout(layout, { rollback: false, revision, state });
      // Keep only one native request behind the in-flight call. Slider input can arrive much
      // faster than Windows can rebuild its visible/click-through region, so pending intermediate
      // targets are superseded instead of forming the previous one-second backlog.
      return enqueueNative({ state, revision, layout, previewed });
    },
    snapshot() {
      return Object.freeze({ requestedRevision, currentState });
    },
  });
}
