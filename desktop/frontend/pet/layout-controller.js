export function createLayoutController({
  computeLayout,
  applyNativeLayout,
  commitLayout,
  initialRevision = 0,
}) {
  if (!Number.isSafeInteger(initialRevision) || initialRevision < 0) {
    throw new Error("initial layout revision must be a non-negative safe integer");
  }
  let requestedRevision = initialRevision;
  let currentState = null;

  return Object.freeze({
    async transition(state, placeholderText = "", input = undefined) {
      const revision = ++requestedRevision;
      currentState = state;
      const layout = computeLayout(state, placeholderText, input);

      const nativeResult = await applyNativeLayout({ state, revision, layout });

      if (revision !== requestedRevision || state !== currentState || !nativeResult.applied) {
        return Object.freeze({ applied: false, revision, state });
      }

      if (nativeResult.contractVersion !== layout.contractVersion) {
        throw new Error("Rust and WebView layout contracts do not match");
      }
      commitLayout(layout, nativeResult);
      return Object.freeze({ applied: true, revision, state, nativeResult });
    },
    snapshot() {
      return Object.freeze({ requestedRevision, currentState });
    },
  });
}
