import assert from "node:assert/strict";
import test from "node:test";

import {
  constrainedPortraitScale,
  createAppearanceMutationGuard,
} from "../pet/appearance.js";

const portraitRect = [150, 328, 600, 656];
const windowSize = [900, 1774];

function renderedSize(sourceSize, requestedPercent = 100) {
  const contain = Math.min(portraitRect[2] / sourceSize[0], portraitRect[3] / sourceSize[1]);
  const scale = constrainedPortraitScale({
    requestedPercent,
    sourceSize,
    portraitRect,
    windowSize,
  });
  return [sourceSize[0] * contain * scale, sourceSize[1] * contain * scale];
}

test("wide tall square and extreme portraits remain contained at normal scale", () => {
  for (const sourceSize of [
    [1600, 954],
    [954, 1600],
    [1600, 1600],
    [1600, 200],
    [200, 1600],
  ]) {
    const [width, height] = renderedSize(sourceSize);
    assert.ok(width <= portraitRect[2] + Number.EPSILON);
    assert.ok(height <= portraitRect[3] + Number.EPSILON);
  }
});

test("portrait scaling stays bounded across the supported range", () => {
  for (const requestedPercent of [50, 100, 150]) {
    for (const sourceSize of [[1600, 954], [954, 1600], [1600, 200]]) {
      const [width, height] = renderedSize(sourceSize, requestedPercent);
      assert.ok(width <= windowSize[0] + Number.EPSILON);
      assert.ok(height <= windowSize[1] + Number.EPSILON);
    }
  }
});

test("a newer layout frame supersedes a full appearance publication still awaiting preparation", async () => {
  const guard = createAppearanceMutationGuard();
  const delayedPublication = guard.begin();
  let releasePreparation;
  const preparation = new Promise((resolve) => { releasePreparation = resolve; });
  const commits = [];
  const delayedCommit = preparation.then(() => {
    if (guard.isCurrent(delayedPublication)) commits.push("stale-layout");
  });

  guard.supersede();
  releasePreparation();
  await delayedCommit;

  assert.deepEqual(commits, []);
});
