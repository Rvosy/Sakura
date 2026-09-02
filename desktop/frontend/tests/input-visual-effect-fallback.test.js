import assert from "node:assert/strict";
import test from "node:test";

import { inputVisualEffectFallbackNotice } from "../pet/input-visual-effect.js";

test("requested native effect falling back to solid points users to the runtime log", () => {
  const notice = inputVisualEffectFallbackNotice(
    { visualEffectMode: "gaussian_blur" },
    { effectiveMode: "solid", outcome: "degraded", errorCode: "WINDOWS_ADVANCED_EFFECTS_DISABLED" },
  );
  assert.match(notice, /右键桌宠/);
  assert.match(notice, /运行日志/);
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
