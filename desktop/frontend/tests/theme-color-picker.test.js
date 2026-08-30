import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  drawHueSurface,
  drawSaturationValueSurface,
} from "../settings/theme-color-picker.js";

const settingsSource = await readFile(
  new URL("../settings/settings.js", import.meta.url),
  "utf8",
);

function makeCanvas(width, height) {
  const fills = [];
  const gradients = [];
  const context = {
    fillStyle: "",
    setTransform(...args) { this.transform = args; },
    clearRect() {},
    fillRect() { fills.push(this.fillStyle); },
    createLinearGradient(...args) {
      const gradient = {
        args,
        stops: [],
        addColorStop(offset, color) { this.stops.push([offset, color]); },
      };
      gradients.push(gradient);
      return gradient;
    },
  };
  return {
    canvas: {
      width: 0,
      height: 0,
      getBoundingClientRect: () => ({ width, height }),
      getContext: () => context,
    },
    context,
    fills,
    gradients,
  };
}

test("saturation/value surface combines hue, white saturation, and black value layers", () => {
  const fixture = makeCanvas(300, 174);
  drawSaturationValueSurface(fixture.canvas, 330, 2);

  assert.equal(fixture.canvas.width, 600);
  assert.equal(fixture.canvas.height, 348);
  assert.deepEqual(fixture.context.transform, [2, 0, 0, 2, 0, 0]);
  assert.equal(fixture.fills[0], "hsl(330 100% 50%)");
  assert.deepEqual(fixture.gradients[0].stops, [
    [0, "#fff"],
    [1, "rgba(255, 255, 255, 0)"],
  ]);
  assert.deepEqual(fixture.gradients[1].stops, [
    [0, "rgba(0, 0, 0, 0)"],
    [1, "#000"],
  ]);
});

test("hue surface contains the full color wheel without CSS gradients", () => {
  const fixture = makeCanvas(300, 20);
  drawHueSurface(fixture.canvas, 1);

  assert.deepEqual(fixture.gradients[0].stops, [
    [0, "#f00"],
    [1 / 6, "#ff0"],
    [2 / 6, "#0f0"],
    [3 / 6, "#0ff"],
    [4 / 6, "#00f"],
    [5 / 6, "#f0f"],
    [1, "#f00"],
  ]);
});

test("theme swatch opens its dialog without depending on unrelated page state", () => {
  const openPopover = settingsSource.match(
    /function openThemeColorPopover\(id\) \{[\s\S]*?\n\}/,
  )?.[0] || "";

  assert.match(openPopover, /popover\.showModal\(\)/);
  assert.doesNotMatch(openPopover, /\bpage\b/);
});
