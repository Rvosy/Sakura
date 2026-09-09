import assert from "node:assert/strict";
import test from "node:test";
import { createPetOverlaySurfaces } from "../pet/overlay-surfaces.js";

test("tooltip updates preserve the open tool dock and release only their own native region", async () => {
  const calls = [];
  const surfaces = createPetOverlaySurfaces(async (_command, payload) => calls.push(payload));
  const dock = [130, 882, 216, 104];
  const tip = [680, 840, 140, 34];
  await surfaces.setToolDock(dock);
  await surfaces.setTooltip(tip);
  await surfaces.setTooltip(null);
  await surfaces.setToolDock(null);
  assert.deepEqual(calls, [
    { rect: dock, tooltipRect: null }, { rect: dock, tooltipRect: tip },
    { rect: dock, tooltipRect: null }, { rect: null, tooltipRect: null },
  ]);
});

test("a late native open completes before cleanup, including after a failed update", async () => {
  const calls = [];
  let release;
  const blocked = new Promise(resolve => { release = resolve; });
  const surfaces = createPetOverlaySurfaces(async (_command, payload) => {
    calls.push(payload);
    if (calls.length === 1) { await blocked; throw new Error("window changed"); }
  });
  const opening = surfaces.setTooltip([10, 20, 100, 30]);
  const closing = surfaces.setTooltip(null);
  await Promise.resolve();
  assert.equal(calls.length, 1);
  release();
  await assert.rejects(opening, /window changed/);
  await closing;
  assert.deepEqual(calls.at(-1), { rect: null, tooltipRect: null });
});
