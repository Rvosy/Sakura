const PRESENTATION_READY_STATES = new Set(["ready", "setup_required", "degraded"]);
const TERMINAL_FAILURE_STATES = new Set(["setup_required", "failed"]);
// Core can spend up to 5s stopping the old tree, then 3s/5s on the
// hello/initialize handshakes and 30s reaching stable readiness. Keep the UI
// locked across that complete lifecycle budget.
export const CHARACTER_SWITCH_TIMEOUT_MS = 60_000;

export function hasCharacterScopedDrafts({
  appearanceDirty = false,
  voiceDirty = false,
  memorySettingsDirty = false,
  memoryDraft = null,
  memoryEditorDraftCount = 0,
} = {}) {
  return Boolean(
    appearanceDirty
    || voiceDirty
    || memorySettingsDirty
    || memoryDraft
    || memoryEditorDraftCount > 0
  );
}

export function countCharacterScopedCollectionDrafts(states = []) {
  let count = 0;
  for (const state of states) {
    if (state?.surface === "memory" && state.editor) count += 1;
  }
  return count;
}

export function setCharacterSwitchLock({ pages = [], submitControls = [] } = {}, switching) {
  const locked = Boolean(switching);
  pages.filter(Boolean).forEach((page) => {
    page.inert = locked;
    page.setAttribute?.("aria-busy", String(locked));
  });
  submitControls.filter(Boolean).forEach((control) => {
    control.disabled = locked;
  });
}

export function syncCharacterEditorControl(control, disabled) {
  if (!control) return;
  control.disabled = Boolean(disabled);
  control.removeAttribute?.("title");
  control.removeAttribute?.("aria-disabled");
}

export function pendingCharacterSelection({
  committedCharacterId = "",
  selectedCharacterId = "",
} = {}) {
  const committed = String(committedCharacterId || "");
  const selected = String(selectedCharacterId || "");
  return selected && selected !== committed ? selected : null;
}

export async function commitCharacterSelection({
  committedCharacterId,
  selectedCharacterId,
  readLifecycle,
  selectCharacter,
  applyChange,
}) {
  const targetCharacterId = pendingCharacterSelection({
    committedCharacterId,
    selectedCharacterId,
  });
  if (!targetCharacterId) return null;
  const previousLifecycle = await readLifecycle();
  const receipt = await selectCharacter(targetCharacterId);
  return applyChange(receipt, previousLifecycle);
}

export async function applyCharacterCatalogChange({
  generationId = "",
  readLifecycle,
  readCatalog,
  applyCatalog,
  rebindSettings,
}) {
  const announcedGenerationId = typeof generationId === "string" ? generationId : "";
  if (!announcedGenerationId) {
    applyCatalog(await readCatalog());
    return true;
  }

  const lifecycle = await readLifecycle();
  if (
    lifecycle?.supervisor?.generationId !== announcedGenerationId
    || lifecycle?.snapshot?.generationId !== announcedGenerationId
    || lifecycle?.characterPresentation?.generationId !== announcedGenerationId
    || !PRESENTATION_READY_STATES.has(lifecycle?.snapshot?.readiness)
  ) return false;

  await rebindSettings(lifecycle);
  return true;
}

export async function waitForCharacterSwitch({
  receipt,
  previousGenerationNumber,
  readLifecycle,
  delay,
  now = () => Date.now(),
  timeoutMs = CHARACTER_SWITCH_TIMEOUT_MS,
}) {
  if (receipt?.restartState !== "requested") return null;
  if (
    !Number.isSafeInteger(previousGenerationNumber)
    || typeof receipt.previousCoreGenerationId !== "string"
    || !receipt.previousCoreGenerationId
    || typeof receipt.targetCharacterId !== "string"
    || !receipt.targetCharacterId
  ) throw new Error("CHARACTER_SWITCH_IDENTITY_INVALID");

  const deadline = now() + timeoutMs;
  while (now() < deadline) {
    const lifecycle = await readLifecycle().catch(() => null);
    const supervisor = lifecycle?.supervisor;
    const snapshot = lifecycle?.snapshot;
    const presentation = lifecycle?.characterPresentation;
    const generationChanged = Number.isSafeInteger(supervisor?.generationNumber)
      && supervisor.generationNumber > previousGenerationNumber
      && supervisor.generationId !== receipt.previousCoreGenerationId;
    const generationConsistent = generationChanged
      && snapshot?.generationId === supervisor.generationId
      && presentation?.generationId === supervisor.generationId;
    if (
      generationConsistent
      && PRESENTATION_READY_STATES.has(snapshot.readiness)
      && presentation.characterId === receipt.targetCharacterId
    ) return lifecycle;
    if (
      generationChanged
      && snapshot?.generationId === supervisor.generationId
      && TERMINAL_FAILURE_STATES.has(snapshot.readiness)
    ) throw new Error("CHARACTER_SWITCH_INITIALIZATION_FAILED");
    await delay(100);
  }
  throw new Error("CHARACTER_SWITCH_TIMEOUT");
}

export async function applyCharacterSwitch({
  receipt,
  previousLifecycle,
  applyCommittedSnapshot,
  clearCharacterState,
  rebindSettings,
  setSwitching,
  readLifecycle,
  delay,
  now,
  timeoutMs,
}) {
  applyCommittedSnapshot(receipt);
  if (receipt?.restartState !== "requested") return null;
  const previousGenerationNumber = previousLifecycle?.supervisor?.generationNumber;
  if (!Number.isSafeInteger(previousGenerationNumber)) {
    throw new Error("CHARACTER_SWITCH_IDENTITY_INVALID");
  }
  setSwitching(true);
  try {
    clearCharacterState();
    const lifecycle = await waitForCharacterSwitch({
      receipt,
      previousGenerationNumber,
      readLifecycle,
      delay,
      now,
      timeoutMs,
    });
    await rebindSettings(lifecycle);
    return lifecycle;
  } finally {
    setSwitching(false);
  }
}
