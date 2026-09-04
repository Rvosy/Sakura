import { createChatPresentationReducer } from "../chat/chat-presentation.js";

export function rebindCharacterPresentation({
  currentCharacterId,
  nextPresentation,
  currentReducer,
}) {
  if (nextPresentation.characterId === currentCharacterId) {
    const rebound = currentReducer.rebindPortraits({
      validPortraitKeys: nextPresentation.portraitKeys,
      defaultPortraitKey: nextPresentation.defaultPortraitKey,
      concernedPortraitKey: nextPresentation.concernedPortraitKey,
    });
    if (!rebound.applied) throw new Error("CHARACTER_PRESENTATION_REBIND_INVALID");
    return Object.freeze({
      characterChanged: false,
      reducer: currentReducer,
      greetingPending: false,
    });
  }
  return Object.freeze({
    characterChanged: true,
    reducer: createChatPresentationReducer({
      initialMessage: nextPresentation.initialMessage,
      defaultPortraitKey: nextPresentation.defaultPortraitKey,
      thinkingPortraitKey: nextPresentation.thinkingPortraitKey,
      concernedPortraitKey: nextPresentation.concernedPortraitKey,
    }),
    greetingPending: true,
  });
}
