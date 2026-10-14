import assert from "node:assert/strict";
import test from "node:test";

import { shouldRevealBubbleAfterNativeDrag } from "../pet/native-drag.js";

test("a hidden bubble is revealed only when the native gesture ends without moving", () => {
  const initialAnchor = { x: 800, y: 900 };
  assert.equal(shouldRevealBubbleAfterNativeDrag({
    bubbleWasHidden: true,
    initialAnchor,
    result: { portraitAnchor: { x: 800, y: 900 } },
  }), true);
  assert.equal(shouldRevealBubbleAfterNativeDrag({
    bubbleWasHidden: true,
    initialAnchor,
    result: { portraitAnchor: { x: 824, y: 916 } },
  }), false);
  assert.equal(shouldRevealBubbleAfterNativeDrag({
    bubbleWasHidden: false,
    initialAnchor,
    result: { portraitAnchor: { x: 800, y: 900 } },
  }), false);
});
