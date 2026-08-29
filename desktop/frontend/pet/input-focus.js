const FOCUSABLE_STATES = new Set(["product"]);

export function createInputFocusController({
  focusInput,
  readText,
  emptySubmissionText = () => "",
  localSubmit,
}) {
  let presentation = "product";
  let composing = false;
  let compositionText = "";
  let wantsFocus = false;
  let inputFocused = false;
  let windowFocused = true;
  let visible = true;

  function canFocus() {
    return FOCUSABLE_STATES.has(presentation) && wantsFocus && windowFocused && visible;
  }

  function restoreFocus(reason) {
    if (!canFocus()) return false;
    focusInput(reason);
    return true;
  }

  function submit(source) {
    if (composing || !FOCUSABLE_STATES.has(presentation)) return false;
    let text = String(readText()).trim();
    if (!text) text = String(emptySubmissionText()).trim();
    if (!text) return false;
    localSubmit(Object.freeze({ text, source }));
    return true;
  }

  return Object.freeze({
    setPresentation(state) {
      presentation = state;
      if (!FOCUSABLE_STATES.has(state)) {
        composing = false;
        compositionText = "";
        inputFocused = false;
        return false;
      }
      wantsFocus = true;
      return restoreFocus("presentation");
    },
    handleCompositionStart(text = "") {
      composing = true;
      compositionText = String(text);
    },
    handleCompositionUpdate(text = "") {
      if (composing) compositionText = String(text);
    },
    handleCompositionEnd(text = "") {
      composing = false;
      compositionText = String(text);
    },
    handleInputFocus() {
      inputFocused = true;
      wantsFocus = true;
    },
    handleInputBlur() {
      inputFocused = false;
    },
    dismissFocus() {
      wantsFocus = false;
      inputFocused = false;
    },
    handleWindowFocus() {
      windowFocused = true;
      return restoreFocus("window-focus");
    },
    handleWindowBlur() {
      windowFocused = false;
      inputFocused = false;
    },
    handleVisibility(nextVisible) {
      visible = Boolean(nextVisible);
      if (!visible) {
        inputFocused = false;
        return false;
      }
      return restoreFocus("visibility");
    },
    handleKeyDown(event) {
      if (
        event.key !== "Enter" ||
        event.shiftKey ||
        composing ||
        event.isComposing
      ) {
        return Object.freeze({ handled: false, submitted: false });
      }
      return Object.freeze({ handled: true, submitted: submit("keyboard") });
    },
    submit,
    snapshot() {
      return Object.freeze({
        presentation,
        composing,
        compositionText,
        wantsFocus,
        inputFocused,
        windowFocused,
        visible,
      });
    },
  });
}
