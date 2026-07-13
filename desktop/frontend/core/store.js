const initialState = () => ({
  bootstrap: null,
  character: null,
  theme: {},
  layout: {},
  subtitle: {},
  interaction: {
    busy: false,
    interactionId: null,
  },
  audio: {
    speaking: false,
    synthesisId: null,
    playbackId: null,
  },
  observation: {
    attached: false,
    observationId: null,
    width: 0,
    height: 0,
  },
});

export function createPetStore() {
  let state = initialState();
  const listeners = new Set();

  function publish() {
    for (const listener of listeners) listener(state);
  }

  return {
    getState() {
      return state;
    },

    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },

    setBootstrap(bootstrap) {
      state = {
        ...state,
        bootstrap,
        character: bootstrap?.character ?? null,
        theme: bootstrap?.theme ?? {},
        layout: bootstrap?.layout ?? {},
        subtitle: bootstrap?.subtitle ?? {},
        interaction: {
          busy: false,
          interactionId: null,
        },
        audio: {
          speaking: false,
          synthesisId: null,
          playbackId: null,
        },
        observation: {
          attached: false,
          observationId: null,
          width: 0,
          height: 0,
        },
      };
      publish();
    },

    setInteractionState(patch) {
      state = {
        ...state,
        interaction: {
          ...state.interaction,
          ...patch,
        },
      };
      publish();
    },

    setAudioState(patch) {
      state = {
        ...state,
        audio: {
          ...state.audio,
          ...patch,
        },
      };
      publish();
    },

    setObservationState(patch) {
      state = {
        ...state,
        observation: {
          ...state.observation,
          ...patch,
        },
      };
      publish();
    },

    clearObservation() {
      state = {
        ...state,
        observation: {
          attached: false,
          observationId: null,
          width: 0,
          height: 0,
        },
      };
      publish();
    },

    resetSession() {
      state = initialState();
      publish();
    },
  };
}

export const petStore = createPetStore();
