import assert from "node:assert/strict";
import test from "node:test";

import {
  createLifecycleReducer,
  projectLifecycle,
  sanitizeDiagnostics,
} from "../lifecycle.js";

const readinessStates = [
  null,
  "transport_ready",
  "initializing",
  "ready",
  "setup_required",
  "degraded",
  "failed",
];

const supervisorCases = [
  ["stopped", "startup"],
  ["spawning", "startup"],
  ["stopping", "core_crashed"],
  ["exited", "core_crashed"],
  ["restarting", "restarting"],
  ["failed", "core_crashed"],
];

const runningReadiness = {
  null: "initializing",
  transport_ready: "initializing",
  initializing: "initializing",
  ready: "ready",
  setup_required: "setup_required",
  degraded: "degraded",
  failed: "failed",
};

function input(supervisorState, readiness, overrides = {}) {
  const { supervisor: supervisorOverrides = {}, snapshot: snapshotOverride, ...rest } = overrides;
  return {
    supervisor: {
      state: supervisorState,
      generationId: "generation-2",
      generationNumber: 2,
      restartPending: false,
      appShutdown: false,
      lastFailure: "unexpected_exit",
      ...supervisorOverrides,
    },
    snapshot: snapshotOverride ??
      (readiness === null
        ? null
        : {
            generationId: "generation-2",
            revision: 4,
            readiness,
          }),
    ...rest,
  };
}

test("every existing Supervisor/readiness combination has a deterministic projection", () => {
  for (const [supervisorState, expectedStatus] of supervisorCases) {
    for (const readiness of readinessStates) {
      assert.equal(
        projectLifecycle(input(supervisorState, readiness)).status,
        expectedStatus,
        `${supervisorState} + ${readiness}`,
      );
    }
  }

  for (const readiness of readinessStates) {
    assert.equal(
      projectLifecycle(input("running", readiness)).status,
      runningReadiness[String(readiness)],
      `running + ${readiness}`,
    );
  }
});

test("manual retry and automatic restart remain visibly distinct", () => {
  assert.equal(
    projectLifecycle(
      input("stopping", "failed", {
        supervisor: { restartPending: true, lastFailure: "deterministic_configuration" },
      }),
    ).status,
    "restarting",
  );
  assert.equal(projectLifecycle(input("restarting", "failed")).status, "restarting");
  assert.equal(
    projectLifecycle(
      input("running", "setup_required", {
        supervisor: { lastFailure: "setup_required" },
      }),
    ).status,
    "setup_required",
  );
  assert.equal(
    projectLifecycle(
      input("running", "degraded", {
        supervisor: { lastFailure: null },
      }),
    ).status,
    "degraded",
  );
});

test("the reducer ignores old generations, mismatched identities, and old Snapshot revisions", () => {
  const reducer = createLifecycleReducer();
  const accepted = reducer.reduce({
    generationId: "generation-2",
    generationNumber: 2,
    revision: 4,
    view: projectLifecycle(input("running", "ready")),
  });
  assert.equal(accepted.applied, true);
  assert.equal(reducer.current().status, "ready");

  for (const stale of [
    {
      generationId: "generation-1",
      generationNumber: 1,
      revision: 99,
      view: { status: "failed" },
    },
    {
      generationId: "wrong-id",
      generationNumber: 2,
      revision: 5,
      view: { status: "failed" },
    },
    {
      generationId: "generation-2",
      generationNumber: 2,
      revision: 3,
      view: { status: "failed" },
    },
  ]) {
    assert.equal(reducer.reduce(stale).applied, false);
    assert.equal(reducer.current().status, "ready");
  }

  assert.equal(
    reducer.reduce({
      generationId: "generation-3",
      generationNumber: 3,
      revision: 0,
      view: { status: "startup" },
    }).applied,
    true,
  );
  assert.equal(reducer.current().status, "startup");
});

test("diagnostics expose only approved stable fields and never echo private input", () => {
  const secret = "sk-live-private";
  const privatePath = "C:\\Users\\private-person\\secrets.yaml";
  const diagnostics = sanitizeDiagnostics({
    status: "failed",
    code: "CORE_FAILED",
    desktopVersion: "0.1.0",
    coreVersion: "2.1.0",
    protocolVersion: "2.1",
    logLocation: "Sakura application logs",
    credential: secret,
    apiKey: secret,
    prompt: `system prompt ${secret}`,
    providerEndpoint: "https://private.invalid/v1",
    model: "private-model",
    config: { path: privatePath },
    exception: `RuntimeError(${secret}) at ${privatePath}`,
  });

  assert.deepEqual(Object.keys(diagnostics).sort(), [
    "code",
    "coreVersion",
    "desktopVersion",
    "logLocation",
    "protocolVersion",
    "status",
  ]);
  const encoded = JSON.stringify(diagnostics);
  for (const forbidden of [secret, privatePath, "private.invalid", "private-model", "RuntimeError"])
    assert.equal(encoded.includes(forbidden), false, forbidden);
});
