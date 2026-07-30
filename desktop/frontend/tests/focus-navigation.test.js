import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const index = readFileSync(new URL("../index.html", import.meta.url), "utf8");

function openingTag(id) {
  return index.match(new RegExp(`<[^>]+id="${id}"[^>]*>`))?.[0] || "";
}

test("the pet surface stays out of sequential tab navigation", () => {
  const bubbleCopy = openingTag("bubble-copy");

  assert.ok(bubbleCopy, "bubble copy markup should exist");
  assert.doesNotMatch(bubbleCopy, /\btabindex\s*=/i);

  for (const id of ["typewriter-skip", "close-window", "composer-input", "composer-send"]) {
    const control = openingTag(id);
    assert.ok(control, `${id} markup should exist`);
    assert.match(control, /\btabindex="-1"/i, id);
  }
});
