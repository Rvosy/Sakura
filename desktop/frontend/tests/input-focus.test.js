import assert from "node:assert/strict";
import test from "node:test";

const inputFocus = await import("../pet/input-focus.js").catch(() => null);

function harness(initialText = "hello") {
  const focusReasons = [];
  const submissions = [];
  let text = initialText;
  const controller = inputFocus.createInputFocusController({
    focusInput: (reason) => focusReasons.push(reason),
    readText: () => text,
    localSubmit: (payload) => submissions.push(payload),
  });
  return { controller, focusReasons, submissions, setText: (value) => (text = value) };
}

test("composer accepts ordinary text and submits only local technical feedback", () => {
  assert.ok(inputFocus, "input-focus module must exist");
  const { controller, submissions } = harness("  hello Sakura  ");
  controller.setPresentation("composer");
  const result = controller.handleKeyDown({ key: "Enter", isComposing: false, shiftKey: false });
  assert.deepEqual(result, { handled: true, submitted: true });
  assert.deepEqual(submissions, [{ text: "hello Sakura", source: "keyboard" }]);
});

test("IME composition updates never become a submit action", () => {
  assert.ok(inputFocus, "input-focus module must exist");
  const { controller, submissions } = harness("樱花");
  controller.setPresentation("composer");
  controller.handleCompositionStart("y");
  controller.handleCompositionUpdate("ying hua");
  assert.deepEqual(
    controller.handleKeyDown({ key: "Enter", isComposing: true, shiftKey: false }),
    { handled: false, submitted: false },
  );
  assert.equal(controller.submit("button"), false);
  controller.handleWindowBlur();
  controller.setPresentation("idle");
  assert.deepEqual(submissions, []);
  assert.equal(controller.snapshot().composing, false);
});

test("composition end permits a later explicit submit", () => {
  assert.ok(inputFocus, "input-focus module must exist");
  const { controller, submissions } = harness("中文");
  controller.setPresentation("composer");
  controller.handleCompositionStart("zhong");
  controller.handleCompositionUpdate("中文");
  controller.handleCompositionEnd("中文");
  assert.equal(controller.submit("button"), true);
  assert.deepEqual(submissions, [{ text: "中文", source: "button" }]);
});

test("Alt+Tab, hide/show, and state round-trips restore focus deterministically", () => {
  assert.ok(inputFocus, "input-focus module must exist");
  const { controller, focusReasons } = harness();
  controller.setPresentation("composer");
  controller.handleInputFocus();
  controller.handleWindowBlur();
  controller.handleInputBlur();
  controller.handleWindowFocus();
  controller.handleVisibility(false);
  controller.handleVisibility(true);
  controller.setPresentation("idle");
  controller.setPresentation("expanded");
  assert.deepEqual(focusReasons, ["presentation", "window-focus", "visibility", "presentation"]);
});

test("empty text, inactive states, and Shift+Enter do not submit", () => {
  assert.ok(inputFocus, "input-focus module must exist");
  const { controller, submissions, setText } = harness("");
  assert.equal(controller.submit("button"), false);
  controller.setPresentation("composer");
  setText("   ");
  assert.equal(controller.submit("button"), false);
  assert.deepEqual(
    controller.handleKeyDown({ key: "Enter", isComposing: false, shiftKey: true }),
    { handled: false, submitted: false },
  );
  assert.deepEqual(submissions, []);
});
