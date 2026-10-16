import assert from "node:assert/strict";
import test from "node:test";
import { checkUpdates } from "../chat/update-check.js";

function catalogResult() {
  const ids = ["enabled", "disabled", "bundled", "conflict", "absent", "latest", "missing-service"];
  return {
    context: { api: 4, services: [] },
    catalog: { schema_version: 1, plugins: ids.map(id => ({ id, versions: [{ version: "1.1.0", package: {},
      manifest: { api: 4, id, name: id, requires: id === "missing-service" ? ["unavailable"] : [] },
    }] })) },
    installed: ids.filter(id => id !== "absent").map(id => ({ pluginId: id, version: id === "latest" ? "1.1.0" : "1.0.0",
      source: id === "bundled" ? "bundled" : "user", enabled: id !== "disabled", reasonCode: id === "conflict" ? "PLUGIN_ID_CONFLICT" : "READY" })),
  };
}

test("startup groups only installed actionable updates, including disabled plugins", async () => {
  let candidates;
  const result = await checkUpdates(async (name, args) => {
    if (name === "startup_update_check") return { status: "up_to_date" };
    if (name === "startup_plugin_catalog") return catalogResult();
    candidates = args.plugins;
    return { status: "pending" };
  }, error => assert.fail(error));
  assert.equal(result.status, "pending");
  assert.deepEqual(candidates.map(item => item.id), ["enabled", "disabled"]);
  assert.equal(candidates[0].currentVersion, "1.0.0");
  assert.equal(candidates[0].version, "1.1.0");
});

test("a failed plugin check does not discard an application update", async () => {
  const errors = [];
  const result = await checkUpdates(async name => {
    if (name === "startup_update_check") return { status: "pending", version: "2.0.0" };
    throw new Error("catalog unavailable");
  }, error => errors.push(error));
  assert.equal(result.status, "pending");
  assert.equal(errors[0].message, "catalog unavailable");
});

test("a disabled automatic check does not publish candidates", async () => {
  const result = await checkUpdates(async name => {
    if (name === "startup_update_check") return { status: "disabled" };
    if (name === "startup_plugin_catalog") return null;
    assert.fail("must not publish candidates");
  }, error => assert.fail(error));
  assert.equal(result.status, "disabled");
});
