import { isChatReadyLifecycle, projectLifecycle } from "../lifecycle.js";

const TERMINALS = new Set(["chat.completed", "chat.failed", "chat.cancelled"]);
const STABLE_LIFECYCLE = new Set(["ready", "setup_required", "degraded", "failed"]);

function validateChatEvent(value) {
  if (
    !value
    || !["chat.started", ...TERMINALS].includes(value.type)
    || typeof value.generationId !== "string"
    || !Number.isSafeInteger(value.generationNumber)
    || value.generationNumber < 1
    || typeof value.operationId !== "string"
    || !value.operationId
  ) throw new Error("CHAT_EVENT_INVALID");
  return Object.freeze(value);
}

function validateSend(value) {
  if (
    value?.accepted !== true
    || typeof value.operationId !== "string"
    || !value.operationId
    || typeof value.cancelHandle !== "string"
    || !value.cancelHandle
    || typeof value.generationId !== "string"
    || !Number.isSafeInteger(value.generationNumber)
    || value.generationNumber < 1
  ) throw new Error("CHAT_SEND_RESPONSE_INVALID");
  return Object.freeze(value);
}

export function createRealChatClient({
  invoke,
  createChannel,
  onCancelError = () => {},
  onEvent,
  prepareGeneration = async () => true,
  initialPreparedGenerationId = null,
  pollIntervalMs = 120,
}) {
  let disposed = false;
  let lifecycleTimer = null;
  let lifecycleBusy = false;
  let lifecycleSignature = "";
  let lifecycleRevision = 0;
  let lifecycleStatus = "startup";
  let currentIdentity = null;
  let interactionEpoch = 0;
  let preparedGenerationId = null;
  let preparedCharacterId = null;
  let interaction = null;

  const sameIdentity = (generationId, generationNumber) => Boolean(
    currentIdentity
    && currentIdentity.generationId === generationId
    && currentIdentity.generationNumber === generationNumber
  );

  function sealInteraction() {
    interactionEpoch += 1;
    if (interaction) interaction.channel.onmessage = () => {};
    interaction = null;
  }

  function acceptIdentity(supervisor) {
    if (
      typeof supervisor?.generationId !== "string"
      || !supervisor.generationId
      || !Number.isSafeInteger(supervisor.generationNumber)
      || supervisor.generationNumber < 1
    ) return false;
    if (currentIdentity) {
      if (supervisor.generationNumber < currentIdentity.generationNumber) return false;
      if (
        supervisor.generationNumber === currentIdentity.generationNumber
        && supervisor.generationId !== currentIdentity.generationId
      ) return false;
    }
    if (!sameIdentity(supervisor.generationId, supervisor.generationNumber)) {
      sealInteraction();
      currentIdentity = Object.freeze({
        generationId: supervisor.generationId,
        generationNumber: supervisor.generationNumber,
      });
      lifecycleRevision = 0;
      lifecycleSignature = "";
      preparedGenerationId = supervisor.generationId === initialPreparedGenerationId
        ? supervisor.generationId
        : null;
      preparedCharacterId = null;
      initialPreparedGenerationId = null;
    }
    return true;
  }

  function emitLifecycle(status, supervisor, signature, canRetry = false, failure = null) {
    if (signature === lifecycleSignature) return;
    lifecycleSignature = signature;
    lifecycleStatus = status;
    lifecycleRevision += 1;
    onEvent(Object.freeze({
      type: "lifecycle",
      status,
      generationId: supervisor.generationId,
      generationNumber: supervisor.generationNumber,
      revision: lifecycleRevision,
      canRetry: Boolean(canRetry),
      failure,
    }));
  }

  function lifecycleSignatureFor(publication, status) {
    const supervisor = publication.supervisor;
    return JSON.stringify([
      supervisor.generationId,
      supervisor.generationNumber,
      supervisor.state,
      supervisor.failure?.code,
      supervisor.failure?.message,
      publication.snapshot?.revision,
      publication.snapshot?.readiness,
      status,
    ]);
  }

  async function pollLifecycle() {
    if (disposed || lifecycleBusy) return;
    lifecycleBusy = true;
    try {
      let publication = await invoke("runtime_lifecycle_snapshot");
      let supervisor = publication?.supervisor;
      if (!acceptIdentity(supervisor)) return;
      let view = projectLifecycle(publication);

      if (isChatReadyLifecycle(lifecycleStatus) && !isChatReadyLifecycle(view.status)) sealInteraction();

      const snapshotMatches = publication.snapshot?.generationId === supervisor.generationId;
      const characterId = publication.characterPresentation?.characterId || null;
      const characterChanged = characterId !== preparedCharacterId;
      if (
        STABLE_LIFECYCLE.has(view.status)
        && snapshotMatches
        && (preparedGenerationId !== supervisor.generationId || characterChanged)
      ) {
        if (characterChanged) sealInteraction();
        emitLifecycle("rehydrating", supervisor, lifecycleSignatureFor(publication, "rehydrating"), false, null);
        const attemptIdentity = currentIdentity;
        const attemptEpoch = interactionEpoch;
        const preparationRequired = isChatReadyLifecycle(view.status);
        let prepared = false;
        try {
          prepared = await prepareGeneration(Object.freeze({
            generationId: supervisor.generationId,
            generationNumber: supervisor.generationNumber,
            snapshotRevision: publication.snapshot.revision,
            characterId,
            refresh: preparedGenerationId === supervisor.generationId,
          })) !== false;
        } catch {
          prepared = false;
        }
        if (
          disposed
          || currentIdentity !== attemptIdentity
          || interactionEpoch !== attemptEpoch
        ) return;
        if (!prepared && preparationRequired) return;
        if (!prepared) {
          emitLifecycle(view.status, supervisor, lifecycleSignatureFor(publication, view.status), view.canRetry, view.failure);
          return;
        }

        publication = await invoke("runtime_lifecycle_snapshot");
        supervisor = publication?.supervisor;
        if (
          !sameIdentity(supervisor?.generationId, supervisor?.generationNumber)
          || publication.snapshot?.generationId !== supervisor.generationId
          || (publication.characterPresentation?.characterId || null) !== characterId
        ) return;
        view = projectLifecycle(publication);
        if (!STABLE_LIFECYCLE.has(view.status)) return;
        preparedGenerationId = supervisor.generationId;
        preparedCharacterId = characterId;
      }
      emitLifecycle(view.status, supervisor, lifecycleSignatureFor(publication, view.status), view.canRetry, view.failure);
    } finally {
      lifecycleBusy = false;
    }
  }

  async function cancelActive(current) {
    if (disposed || interaction !== current || !current.response || !isChatReadyLifecycle(lifecycleStatus)) return false;
    const result = await invoke("chat_cancel", { payload: {
      operationId: current.response.operationId,
      cancelHandle: current.response.cancelHandle,
    } });
    return interaction === current
      && isChatReadyLifecycle(lifecycleStatus)
      && result?.operationId === current.operationId
      && Boolean(result.accepted);
  }

  function receive(current, value) {
    if (disposed || interaction !== current || current.terminal) return;
    let event;
    try {
      event = validateChatEvent(value);
    } catch {
      return;
    }
    if (
      !isChatReadyLifecycle(lifecycleStatus)
      || !sameIdentity(event.generationId, event.generationNumber)
      || (current.operationId && current.operationId !== event.operationId)
    ) return;
    current.operationId = event.operationId;
    if (event.type === "chat.started") {
      if (current.started) return;
      current.started = true;
    }
    if (TERMINALS.has(event.type)) {
      current.terminal = true;
      current.channel.onmessage = () => {};
      if (current.response) interaction = null;
    }
    onEvent(Object.freeze({ ...event, presentation: current.presentation }));
  }

  async function sendCommand(command, args, presentation) {
    if (disposed) throw new Error("CHAT_CLIENT_DISPOSED");
    if (interaction) throw new Error("CHAT_INTERACTION_ACTIVE");
    if (!currentIdentity || !isChatReadyLifecycle(lifecycleStatus)) throw new Error("CHAT_NOT_READY");
    if (!["interactive", "silent"].includes(presentation)) throw new Error("CHAT_PRESENTATION_INVALID");
    const current = {
      identity: currentIdentity,
      epoch: interactionEpoch,
      presentation,
      operationId: null,
      response: null,
      started: false,
      terminal: false,
      cancelRequested: false,
      channel: createChannel(),
    };
    interaction = current;
    current.channel.onmessage = (event) => receive(current, event);
    try {
      const response = validateSend(await invoke(command, { ...args, onEvent: current.channel }));
      if (
        disposed
        || current.identity !== currentIdentity
        || current.epoch !== interactionEpoch
        || !isChatReadyLifecycle(lifecycleStatus)
        || !sameIdentity(response.generationId, response.generationNumber)
      ) throw new Error("CHAT_GENERATION_INVALIDATED");
      if (current.operationId && current.operationId !== response.operationId) throw new Error("CHAT_SEND_RESPONSE_INVALID");
      current.operationId = response.operationId;
      current.response = response;
      if (current.terminal) interaction = null;
      else if (current.cancelRequested) {
        // Cancellation has its own result; its failure cannot turn an accepted send
        // into a submission failure or cause the draft to be sent a second time.
        void cancelActive(current).catch(onCancelError);
      }
      return response;
    } catch (error) {
      current.channel.onmessage = () => {};
      if (interaction === current) interaction = null;
      throw error;
    }
  }

  return Object.freeze({
    async start() {
      if (disposed) throw new Error("CHAT_CLIENT_DISPOSED");
      await pollLifecycle();
      lifecycleTimer = window.setInterval(() => pollLifecycle().catch(() => {}), pollIntervalMs);
    },
    async send({ message, attachmentId = null, presentation = "interactive" }) {
      const payload = attachmentId ? { message, attachmentId } : { message };
      return sendCommand("chat_send", { payload }, presentation);
    },
    async announceUpdate() {
      return sendCommand("chat_update_announce", undefined, "silent");
    },
    async observeScreen({ attachmentId }) {
      return sendCommand("chat_screen_observation", { attachmentId }, "silent");
    },
    async cancel(operationId) {
      const current = interaction;
      if (disposed || !current || current.terminal || current.operationId !== operationId) return false;
      if (!current.response) {
        current.cancelRequested = true;
        return true;
      }
      return cancelActive(current);
    },
    isBusy() {
      return Boolean(interaction);
    },
    dispose() {
      disposed = true;
      window.clearInterval(lifecycleTimer);
      lifecycleTimer = null;
      sealInteraction();
    },
  });
}
