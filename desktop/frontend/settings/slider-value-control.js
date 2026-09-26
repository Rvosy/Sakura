export function upgradeSliderControls(document = globalThis.document) {
  document.querySelectorAll(".slider-control").forEach((control) => {
    const output = control.querySelector(".slider-value");
    const slider = control.querySelector("input[type='range']");
    if (!output || !slider || output.dataset.upgraded) return;
    output.dataset.upgraded = "true";

    output.addEventListener("click", () => {
      if (slider.disabled) return;
      const editor = document.createElement("input");
      editor.type = "number";
      editor.className = "slider-value-editor";
      editor.min = slider.min || "0";
      editor.max = slider.max || "100";
      editor.step = slider.step || "1";
      editor.value = slider.value;
      editor.style.width = `${Math.max(40, output.offsetWidth)}px`;
      output.replaceWith(editor);
      editor.focus();
      editor.select();

      let finished = false;
      function finish(commit) {
        // Replacing the focused editor can synchronously fire blur again.
        if (finished) return;
        finished = true;
        if (commit) {
          const number = Number.parseInt(editor.value, 10);
          const clamped = Number.isFinite(number)
            ? Math.min(Number(editor.max), Math.max(Number(editor.min), number))
            : Number(editor.min);
          const changed = String(clamped) !== slider.value;
          slider.value = String(clamped);
          if (changed) slider.dispatchEvent(new Event("input", { bubbles: true }));
        }
        output.textContent = slider.value;
        editor.replaceWith(output);
      }
      editor.addEventListener("blur", () => finish(true));
      editor.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === "Escape") {
          event.preventDefault();
          finish(event.key === "Enter");
        }
      });
    });
  });
}
