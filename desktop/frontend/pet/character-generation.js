import { createChatPresentationReducer } from "../chat/chat-presentation.js";

export function rebindCharacterPresentation({
  currentCharacterId,
  nextPresentation,
  currentReducer,
}) {
  if (nextPresentation.characterId === currentCharacterId) {
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
