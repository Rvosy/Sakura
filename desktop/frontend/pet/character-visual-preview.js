function validPublicationIdentity(publication) {
  return publication?.schemaVersion === 1
    && Number.isSafeInteger(publication.windowGeneration)
    && Number.isSafeInteger(publication.revision);
}

export function createCharacterVisualPreviewSessionController({
  currentCoreGenerationId,
  blocked = () => false,
} = {}) {
  if (typeof currentCoreGenerationId !== "function") {
    throw new Error("currentCoreGenerationId is required");
  }

  let windowGeneration = 0;
  let revision = 0;
  let session = 0;

  const isCurrent = (token) => Boolean(
    token
    && !blocked()
    && token.session === session
    && token.windowGeneration === windowGeneration
    && token.revision === revision
    && token.coreGenerationId === currentCoreGenerationId()
  );

  return Object.freeze({
    begin(publication) {
      if (
        !validPublicationIdentity(publication)
        || blocked()
        || publication.windowGeneration < windowGeneration
        || (
          publication.windowGeneration === windowGeneration
          && publication.revision <= revision
        )
      ) return null;

      if (publication.windowGeneration > windowGeneration) {
        windowGeneration = publication.windowGeneration;
        revision = 0;
      }
      revision = publication.revision;
      session += 1;
      return Object.freeze({
        session,
        windowGeneration,
        revision,
        coreGenerationId: currentCoreGenerationId(),
      });
    },
    invalidate() {
      session += 1;
    },
    isCurrent,
  });
}

export async function restoreCommittedCharacterVisual({
  currentState,
  resetRenderedPortrait,
  render,
} = {}) {
  if (
    typeof currentState !== "function"
    || typeof resetRenderedPortrait !== "function"
    || typeof render !== "function"
  ) throw new Error("character visual restore dependencies are required");

  const latestState = currentState();
  resetRenderedPortrait();
  return render(latestState);
}
