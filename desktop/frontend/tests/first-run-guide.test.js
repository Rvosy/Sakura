import assert from "node:assert/strict";
import test from "node:test";

import { calloutPosition, spotlightGeometry } from "../settings/first-run-guide.js";

test("spotlight geometry matches the target exactly and clips only at the viewport", () => {
  assert.deepEqual(
    spotlightGeometry(
      { left: 2, top: 4, right: 122, bottom: 64, width: 120, height: 60 },
      { width: 800, height: 600 },
    ),
    { left: 2, top: 4, width: 120, height: 60 },
  );
  assert.deepEqual(
    spotlightGeometry(
      { left: -5, top: -4, right: 35, bottom: 26, width: 40, height: 30 },
      { width: 800, height: 600 },
    ),
    { left: 0, top: 0, width: 35, height: 26 },
  );
  assert.equal(spotlightGeometry(null, { width: 800, height: 600 }), null);
});

test("guide copy stays outside a wide spotlight", () => {
  const spotlight = { left: 200, top: 158, width: 758, height: 170 };
  const position = calloutPosition(
    spotlight,
    { width: 980, height: 684 },
    { width: 300, height: 250 },
  );
  assert.ok(position.top >= spotlight.top + spotlight.height);
  assert.ok(position.maxHeight > 0);
});
