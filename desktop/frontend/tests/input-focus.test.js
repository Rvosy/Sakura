import assert from "node:assert/strict";
import test from "node:test";

const inputFocus = await import("../pet/input-focus.js").catch(() => null);

function harness(initialText = "hello", { emptySubmissionText = () => "" } = {}) {
  const focusReasons = [];
  const submissions = [];
  let text = initialText;
  const controller = inputFocus.createInputFocusController({
    focusInput: (reason) => focusReasons.push(reason),
    readText: () => text,
    emptySubmissionText,
    localSubmit: (payload) => submissions.push(payload),
  });
  return { controller, focusReasons, submissions, setText: (value) => (text = value) };
}

test("composer accepts ordinary text and submits only to the injected local presentation consumer", () => {
  assert.ok(inputFocus, "input-focus module must exist");
  const { controller, submissions } = harness("  hello Sakura  ");
  controller.setPresentation("product");
  const result = controller.handleKeyDown({ key: "Enter", isComposing: false, shiftKey: false });
  assert.deepEqual(result, { handled: true, submitted: true });
  assert.deepEqual(submissions, [{ text: "hello Sakura", source: "keyboard" }]);
});

test("an attached screenshot supplies the main-compatible default text for an empty draft", () => {
  assert.ok(inputFocus, "input-focus module must exist");
  const { controller, submissions } = harness("   ", {
    emptySubmissionText: () => "请根据我框选的截图继续对话。",
  });
  controller.setPresentation("product");

  assert.equal(controller.submit("button"), true);
  assert.deepEqual(submissions, [{
    text: "请根据我框选的截图继续对话。",
    source: "button",
  }]);
});

test("IME composition updates never become a submit action", () => {
  assert.ok(inputFocus, "input-focus module must exist");
  const { controller, submissions } = harness("樱花");
  controller.setPresentation("product");
  controller.handleCompositionStart("y");
  controller.handleCompositionUpdate("ying hua");
  assert.deepEqual(
    controller.handleKeyDown({ key: "Enter", isComposing: true, shiftKey: false }),
    { handled: false, submitted: false },
  );
  assert.equal(controller.submit("button"), false);
  controller.handleWindowBlur();
  controller.setPresentation("hidden");
  assert.deepEqual(submissions, []);
  assert.equal(controller.snapshot().composing, false);
});

test("composition end permits a later explicit submit", () => {
  assert.ok(inputFocus, "input-focus module must exist");
  const { controller, submissions } = harness("中文");
  controller.setPresentation("product");
  controller.handleCompositionStart("zhong");
  controller.handleCompositionUpdate("中文");
  controller.handleCompositionEnd("中文");
  assert.equal(controller.submit("button"), true);
  assert.deepEqual(submissions, [{ text: "中文", source: "button" }]);
});

test("window deactivation relinquishes focus until the product presentation requests it again", () => {
  assert.ok(inputFocus, "input-focus module must exist");
  const { controller, focusReasons } = harness();
  controller.setPresentation("product");
  controller.handleInputFocus();
  controller.handleWindowBlur();
  controller.handleInputBlur();
  controller.handleWindowFocus();
  controller.handleVisibility(false);
  controller.handleVisibility(true);
  controller.setPresentation("hidden");
  controller.setPresentation("product");
  assert.deepEqual(focusReasons, ["presentation", "presentation"]);
  assert.equal(controller.snapshot().wantsFocus, true);
});

test("window deactivation clears a focused input even when no DOM blur arrives", () => {
  assert.ok(inputFocus, "input-focus module must exist");
  const { controller } = harness();
  controller.setPresentation("product");
  controller.handleInputFocus();
  controller.handleWindowBlur();
  assert.equal(controller.snapshot().wantsFocus, false);
  assert.equal(controller.snapshot().inputFocused, false);
});

test("an explicit outside-pointer dismissal survives window and visibility round-trips", () => {
  assert.ok(inputFocus, "input-focus module must exist");
  const { controller, focusReasons } = harness();
  controller.setPresentation("product");
  controller.handleInputFocus();
  controller.dismissFocus();
  controller.handleWindowBlur();
  controller.handleWindowFocus();
  controller.handleVisibility(false);
  controller.handleVisibility(true);
  assert.deepEqual(focusReasons, ["presentation"]);
  assert.equal(controller.snapshot().wantsFocus, false);
  assert.equal(controller.snapshot().inputFocused, false);
});

test("empty text, inactive states, and Shift+Enter do not submit", () => {
  assert.ok(inputFocus, "input-focus module must exist");
  const { controller, submissions, setText } = harness("");
  assert.equal(controller.submit("button"), false);
  controller.setPresentation("product");
  setText("   ");
  assert.equal(controller.submit("button"), false);
  assert.deepEqual(
    controller.handleKeyDown({ key: "Enter", isComposing: false, shiftKey: true }),
    { handled: false, submitted: false },
  );
  assert.deepEqual(submissions, []);
});
