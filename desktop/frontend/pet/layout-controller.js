export function createLayoutController({
  computeLayout,
  applyNativeLayout,
  commitLayout,
}) {
  let requestedRevision = 0;
  let currentState = null;

  return Object.freeze({
    async transition(state, placeholderText = "") {
      const revision = ++requestedRevision;
      currentState = state;
      const layout = computeLayout(state, placeholderText);

      const nativeResult = await applyNativeLayout({ state, revision });

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
