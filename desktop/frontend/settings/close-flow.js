export const CloseDecision = Object.freeze({
  SAVE: "save",
  DISCARD: "discard",
  STAY: "stay",
  CLOSE: "close",
});

export async function executeSettingsClose({
  dirty,
  choose,
  save,
  discard,
  close,
  stay = async () => {},
}) {
  const decision = dirty ? await choose() : CloseDecision.CLOSE;
  if (!Object.values(CloseDecision).includes(decision)) {
    throw new Error("SETTINGS_CLOSE_DECISION_INVALID");
  }
  if (decision === CloseDecision.STAY) {
    await stay();
    return decision;
  }
  if (decision === CloseDecision.SAVE) {
    await save();
  } else if (decision === CloseDecision.DISCARD) {
    await discard();
  }
  await close();
  return decision;
}
