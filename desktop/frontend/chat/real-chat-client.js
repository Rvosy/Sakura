import { projectLifecycle } from "../lifecycle.js";

const TERMINALS = new Set(["chat.completed", "chat.failed", "chat.cancelled"]);

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

export function createRealChatClient({ invoke, listen, onEvent, pollIntervalMs = 120 }) {
  let disposed = false;
  let unlisten = null;
  let lifecycleTimer = null;
  let lifecycleBusy = false;
  let lifecycleSignature = "";
  let lifecycleRevision = 0;
  let lifecycleGeneration = 0;
  let pendingSend = false;
  let active = null;
  let pendingCancel = false;
  const earlyTerminals = new Set();

  async function pollLifecycle() {
    if (disposed || lifecycleBusy) return;
    lifecycleBusy = true;
    try {
      const publication = await invoke("runtime_lifecycle_snapshot");
      const supervisor = publication?.supervisor;
      if (!supervisor || !Number.isSafeInteger(supervisor.generationNumber) || supervisor.generationNumber < 1) return;
      const view = projectLifecycle(publication);
      const signature = JSON.stringify([
        supervisor.generationId,
        supervisor.generationNumber,
        supervisor.state,
        supervisor.restartPending,
        supervisor.lastFailure,
        publication.snapshot?.revision,
        publication.snapshot?.readiness,
      ]);
      if (signature === lifecycleSignature) return;
      if (supervisor.generationNumber !== lifecycleGeneration) {
        lifecycleGeneration = supervisor.generationNumber;
        lifecycleRevision = 0;
        active = null;
        pendingCancel = false;
        earlyTerminals.clear();
      }
      lifecycleSignature = signature;
      lifecycleRevision += 1;
      onEvent(Object.freeze({
        type: "lifecycle",
        status: view.status,
        generationId: supervisor.generationId,
        generationNumber: supervisor.generationNumber,
        revision: lifecycleRevision,
      }));
    } finally {
      lifecycleBusy = false;
    }
  }

  async function cancelActive() {
    if (!active || disposed) return false;
    const current = active;
    const result = await invoke("chat_cancel", { payload: {
      operationId: current.operationId,
      cancelHandle: current.cancelHandle,
    } });
    return result?.operationId === current.operationId && Boolean(result.accepted);
  }

  function receive(nativeEvent) {
    if (disposed) return;
    let event;
    try {
      event = validateChatEvent(nativeEvent?.payload);
    } catch {
      return;
    }
    if (TERMINALS.has(event.type)) {
      if (active?.operationId === event.operationId) active = null;
      else earlyTerminals.add(event.operationId);
      pendingCancel = false;
    }
    onEvent(event);
  }

  return Object.freeze({
    async start() {
      if (disposed) throw new Error("CHAT_CLIENT_DISPOSED");
      unlisten = await listen("sakura://chat-event", receive);
      await pollLifecycle();
      lifecycleTimer = window.setInterval(() => void pollLifecycle(), pollIntervalMs);
    },
    async send({ message }) {
      if (disposed) throw new Error("CHAT_CLIENT_DISPOSED");
      if (pendingSend || active) throw new Error("CHAT_INTERACTION_ACTIVE");
      pendingSend = true;
      try {
        const response = validateSend(await invoke("chat_send", { payload: { message } }));
        if (!earlyTerminals.delete(response.operationId)) active = response;
        if (pendingCancel && active) await cancelActive();
        return response;
      } finally {
        pendingSend = false;
      }
    },
    async cancel(operationId) {
      if (disposed) return false;
      if (!active) {
        if (pendingSend && typeof operationId === "string" && operationId) {
          pendingCancel = true;
          return true;
        }
        return false;
      }
      if (active.operationId !== operationId) return false;
      return cancelActive();
    },
    dispose() {
      disposed = true;
      window.clearInterval(lifecycleTimer);
      lifecycleTimer = null;
      active = null;
      earlyTerminals.clear();
      try {
        Promise.resolve(unlisten?.()).catch(() => {});
      } catch {
        // The native event host may already be gone.
      }
    },
  });
}
