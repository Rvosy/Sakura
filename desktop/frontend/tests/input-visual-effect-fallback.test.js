import assert from "node:assert/strict";
import test from "node:test";

import { inputVisualEffectFallbackNotice } from "../pet/input-visual-effect.js";

test("requested native effect falling back to solid reports degraded presentation", () => {
  const notice = inputVisualEffectFallbackNotice(
    { visualEffectMode: "gaussian_blur" },
    { effectiveMode: "solid", outcome: "degraded", errorCode: "WINDOWS_ADVANCED_EFFECTS_DISABLED" },
  );
  assert.ok(notice);
});

test("solid requests and active native effects do not show a fallback notice", () => {
  assert.equal(
    inputVisualEffectFallbackNotice(
      { visualEffectMode: "solid" },
      { effectiveMode: "solid", outcome: "ready" },
    ),
    "",
  );
  assert.equal(
    inputVisualEffectFallbackNotice(
      { visualEffectMode: "gaussian_blur" },
      { effectiveMode: "gaussian_blur", outcome: "ready" },
    ),
    "",
  );
});
