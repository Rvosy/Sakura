import { projectLifecycle } from "../lifecycle.js";

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
  listen,
  onEvent,
  prepareGeneration = async () => true,
  initialPreparedGenerationId = null,
  pollIntervalMs = 120,
}) {
  let disposed = false;
  let unlisten = null;
  let lifecycleTimer = null;
  let lifecycleBusy = false;
  let lifecycleSignature = "";
  let lifecycleRevision = 0;
  let lifecycleStatus = "startup";
  let currentIdentity = null;
  let interactionEpoch = 0;
  let preparedGenerationId = null;
  let pendingSend = null;
  let active = null;
  let pendingCancel = null;
  const earlyTerminals = new Set();

  const sameIdentity = (generationId, generationNumber) => Boolean(
    currentIdentity
    && currentIdentity.generationId === generationId
    && currentIdentity.generationNumber === generationNumber
  );

  function operationKey(generationId, generationNumber, operationId) {
    return `${generationNumber}:${generationId}:${operationId}`;
  }

  function sealInteraction() {
    interactionEpoch += 1;
    active = null;
    pendingSend = null;
    pendingCancel = null;
    earlyTerminals.clear();
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
      initialPreparedGenerationId = null;
    }
    return true;
  }

  function emitLifecycle(status, supervisor, signature, canRetry = false) {
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
    }));
  }

  function lifecycleSignatureFor(publication, status) {
    const supervisor = publication.supervisor;
    return JSON.stringify([
      supervisor.generationId,
      supervisor.generationNumber,
      supervisor.state,
      supervisor.restartPending,
      supervisor.lastFailure,
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

      if (lifecycleStatus === "ready" && view.status !== "ready") sealInteraction();

      const snapshotMatches = publication.snapshot?.generationId === supervisor.generationId;
      if (
        STABLE_LIFECYCLE.has(view.status)
        && snapshotMatches
        && preparedGenerationId !== supervisor.generationId
      ) {
        emitLifecycle("rehydrating", supervisor, lifecycleSignatureFor(publication, "rehydrating"), false);
        const attemptIdentity = currentIdentity;
        const attemptEpoch = interactionEpoch;
        const preparationRequired = view.status === "ready" || view.status === "degraded";
        let prepared = false;
        try {
          prepared = await prepareGeneration(Object.freeze({
            generationId: supervisor.generationId,
            generationNumber: supervisor.generationNumber,
            snapshotRevision: publication.snapshot.revision,
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
          emitLifecycle(view.status, supervisor, lifecycleSignatureFor(publication, view.status), view.canRetry);
          return;
        }

        publication = await invoke("runtime_lifecycle_snapshot");
        supervisor = publication?.supervisor;
        if (
          !sameIdentity(supervisor?.generationId, supervisor?.generationNumber)
          || publication.snapshot?.generationId !== supervisor.generationId
        ) return;
        view = projectLifecycle(publication);
        if (!STABLE_LIFECYCLE.has(view.status)) return;
        preparedGenerationId = supervisor.generationId;
      }
      emitLifecycle(view.status, supervisor, lifecycleSignatureFor(publication, view.status), view.canRetry);
    } finally {
      lifecycleBusy = false;
    }
  }

  async function cancelActive(current) {
    if (!current || disposed || active !== current || lifecycleStatus !== "ready") return false;
    const epoch = interactionEpoch;
    const result = await invoke("chat_cancel", { payload: {
      operationId: current.operationId,
      cancelHandle: current.cancelHandle,
    } });
    return interactionEpoch === epoch
      && active === current
      && lifecycleStatus === "ready"
      && result?.operationId === current.operationId
      && Boolean(result.accepted);
  }

  function receive(nativeEvent) {
    if (disposed) return;
    let event;
    try {
      event = validateChatEvent(nativeEvent?.payload);
    } catch {
      return;
    }
    if (
      lifecycleStatus !== "ready"
      || !sameIdentity(event.generationId, event.generationNumber)
    ) return;
    if (TERMINALS.has(event.type)) {
      if (active?.operationId === event.operationId) active = null;
      else earlyTerminals.add(operationKey(event.generationId, event.generationNumber, event.operationId));
      pendingCancel = null;
    }
    onEvent(event);
  }

  return Object.freeze({
    async start() {
      if (disposed) throw new Error("CHAT_CLIENT_DISPOSED");
      unlisten = await listen("sakura://chat-event", receive);
      await pollLifecycle();
      lifecycleTimer = window.setInterval(() => pollLifecycle().catch(() => {}), pollIntervalMs);
    },
    async send({ message }) {
      if (disposed) throw new Error("CHAT_CLIENT_DISPOSED");
      if (pendingSend || active) throw new Error("CHAT_INTERACTION_ACTIVE");
      if (!currentIdentity || lifecycleStatus !== "ready") throw new Error("CHAT_NOT_READY");
      const token = Object.freeze({ identity: currentIdentity, epoch: interactionEpoch });
      pendingSend = token;
      try {
        const response = validateSend(await invoke("chat_send", { payload: { message } }));
        if (
          token.identity !== currentIdentity
          || token.epoch !== interactionEpoch
          || lifecycleStatus !== "ready"
          || !sameIdentity(response.generationId, response.generationNumber)
        ) throw new Error("CHAT_GENERATION_INVALIDATED");
        const key = operationKey(response.generationId, response.generationNumber, response.operationId);
        if (!earlyTerminals.delete(key)) active = response;
        if (
          pendingCancel?.epoch === interactionEpoch
          && pendingCancel.operationId === response.operationId
          && active
        ) await cancelActive(active);
        return response;
      } finally {
        if (pendingSend === token) pendingSend = null;
      }
    },
    async cancel(operationId) {
      if (disposed) return false;
      if (!active) {
        if (pendingSend && typeof operationId === "string" && operationId) {
          pendingCancel = Object.freeze({ operationId, epoch: interactionEpoch });
          return true;
        }
        return false;
      }
      if (active.operationId !== operationId) return false;
      return cancelActive(active);
    },
    dispose() {
      disposed = true;
      window.clearInterval(lifecycleTimer);
      lifecycleTimer = null;
      sealInteraction();
      try {
        Promise.resolve(unlisten?.()).catch(() => {});
      } catch {
        // The native event host may already be gone.
      }
    },
  });
}
