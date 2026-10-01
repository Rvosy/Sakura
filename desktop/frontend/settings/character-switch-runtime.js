const PRESENTATION_READY_STATES = new Set(["ready", "setup_required", "degraded"]);
const TERMINAL_FAILURE_STATES = new Set(["setup_required", "failed"]);

export function hasCharacterScopedDrafts({
  appearanceDirty = false,
  voiceDirty = false,
  collectionDraftCount = 0,
} = {}) {
  return Boolean(
    appearanceDirty
    || voiceDirty
    || collectionDraftCount > 0
  );
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
}) {
  if (receipt?.restartState !== "requested" && !receipt?.characterChanged) return null;
  const expectedCharacterId = receipt.targetCharacterId || "";
  if (
    !Number.isSafeInteger(previousGenerationNumber)
    || typeof receipt.previousCoreGenerationId !== "string"
    || !receipt.previousCoreGenerationId
    || (
      receipt.targetCharacterId !== null
      && (typeof receipt.targetCharacterId !== "string" || !receipt.targetCharacterId)
    )
  ) throw new Error("CHARACTER_SWITCH_IDENTITY_INVALID");

  while (true) {
    const lifecycle = await readLifecycle();
    const supervisor = lifecycle?.supervisor;
    const snapshot = lifecycle?.snapshot;
    const presentation = lifecycle?.characterPresentation;
    const generationChanged = Number.isSafeInteger(supervisor?.generationNumber)
      && supervisor.generationNumber > previousGenerationNumber
      && supervisor.generationId !== receipt.previousCoreGenerationId;
    const sameGenerationRefresh = Boolean(receipt.characterChanged)
      && supervisor?.generationId === receipt.previousCoreGenerationId;
    if (supervisor?.appShutdown) throw new Error("CHARACTER_SWITCH_CANCELLED");
    if ((generationChanged || sameGenerationRefresh) && supervisor?.state === "stopped") {
      throw new Error("CHARACTER_SWITCH_CANCELLED");
    }
    if ((generationChanged || sameGenerationRefresh) && supervisor?.state === "failed") {
      throw new Error("CHARACTER_SWITCH_INITIALIZATION_FAILED");
    }
    const presentedId = presentation?.characterId || "";
    const presentationMatches = expectedCharacterId
      ? presentation?.generationId === supervisor.generationId
        && presentedId === expectedCharacterId
      : presentedId === ""
        && (!presentation || presentation.generationId === supervisor.generationId);
    if (
      (generationChanged || sameGenerationRefresh)
      && snapshot?.generationId === supervisor.generationId
      && PRESENTATION_READY_STATES.has(snapshot.readiness)
      && presentationMatches
    ) return lifecycle;
    if (
      (generationChanged || (receipt.characterChanged && supervisor?.generationId === receipt.previousCoreGenerationId))
      && snapshot?.generationId === supervisor.generationId
      && TERMINAL_FAILURE_STATES.has(snapshot.readiness)
      && !(expectedCharacterId === "" && snapshot.readiness === "setup_required")
    ) throw new Error("CHARACTER_SWITCH_INITIALIZATION_FAILED");
    await delay(100);
  }
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
}) {
  applyCommittedSnapshot(receipt);
  if (receipt?.restartState !== "requested" && !receipt?.characterChanged) return null;
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
    });
    await rebindSettings(lifecycle);
    return lifecycle;
  } finally {
    setSwitching(false);
  }
}
