import { normalizeColorText } from "../core/theme-runtime.js";

export function hexToRgb(hex) {
  const value = normalizeColorText(hex, "#000000").slice(1);
  return {
    r: Number.parseInt(value.slice(0, 2), 16),
    g: Number.parseInt(value.slice(2, 4), 16),
    b: Number.parseInt(value.slice(4, 6), 16),
  };
}

function componentToHex(value) {
  return Math.round(Math.min(255, Math.max(0, value))).toString(16).padStart(2, "0");
}

export function rgbToHex({ r, g, b }) {
  return `#${componentToHex(r)}${componentToHex(g)}${componentToHex(b)}`;
}

export function rgbToHsv({ r, g, b }) {
  const red = r / 255;
  const green = g / 255;
  const blue = b / 255;
  const max = Math.max(red, green, blue);
  const min = Math.min(red, green, blue);
  const delta = max - min;
  let h = 0;
  if (delta !== 0) {
    if (max === red) {
      h = ((green - blue) / delta) % 6;
    } else if (max === green) {
      h = (blue - red) / delta + 2;
    } else {
      h = (red - green) / delta + 4;
    }
    h *= 60;
    if (h < 0) {
      h += 360;
    }
  }
  return {
    h,
    s: max === 0 ? 0 : delta / max,
    v: max,
  };
}

export function hsvToRgb({ h, s, v }) {
  const chroma = v * s;
  const x = chroma * (1 - Math.abs(((h / 60) % 2) - 1));
  const m = v - chroma;
  let red = 0;
  let green = 0;
  let blue = 0;
  if (h < 60) {
    red = chroma; green = x;
  } else if (h < 120) {
    red = x; green = chroma;
  } else if (h < 180) {
    green = chroma; blue = x;
  } else if (h < 240) {
    green = x; blue = chroma;
  } else if (h < 300) {
    red = x; blue = chroma;
  } else {
    red = chroma; blue = x;
  }
  return {
    r: (red + m) * 255,
    g: (green + m) * 255,
    b: (blue + m) * 255,
  };
}

function prepareCanvas(canvas, pixelRatio) {
  const rect = canvas.getBoundingClientRect();
  const width = Math.max(1, Math.round(rect.width));
  const height = Math.max(1, Math.round(rect.height));
  const ratio = Math.max(1, Number(pixelRatio) || 1);
  const bitmapWidth = Math.round(width * ratio);
  const bitmapHeight = Math.round(height * ratio);

  if (canvas.width !== bitmapWidth) canvas.width = bitmapWidth;
  if (canvas.height !== bitmapHeight) canvas.height = bitmapHeight;

  const context = canvas.getContext("2d");
  context?.setTransform(ratio, 0, 0, ratio, 0, 0);
  return { context, width, height };
}

export function drawSaturationValueSurface(canvas, hue, pixelRatio = globalThis.devicePixelRatio) {
  const { context, width, height } = prepareCanvas(canvas, pixelRatio);
  if (!context) return;

  context.clearRect(0, 0, width, height);
  context.fillStyle = `hsl(${hue} 100% 50%)`;
  context.fillRect(0, 0, width, height);

  const saturation = context.createLinearGradient(0, 0, width, 0);
  saturation.addColorStop(0, "#fff");
  saturation.addColorStop(1, "rgba(255, 255, 255, 0)");
  context.fillStyle = saturation;
  context.fillRect(0, 0, width, height);

  const value = context.createLinearGradient(0, 0, 0, height);
  value.addColorStop(0, "rgba(0, 0, 0, 0)");
  value.addColorStop(1, "#000");
  context.fillStyle = value;
  context.fillRect(0, 0, width, height);
}

export function drawHueSurface(canvas, pixelRatio = globalThis.devicePixelRatio) {
  const { context, width, height } = prepareCanvas(canvas, pixelRatio);
  if (!context) return;

  context.clearRect(0, 0, width, height);
  const hue = context.createLinearGradient(0, 0, width, 0);
  [
    [0, "#f00"],
    [1 / 6, "#ff0"],
    [2 / 6, "#0f0"],
    [3 / 6, "#0ff"],
    [4 / 6, "#00f"],
    [5 / 6, "#f0f"],
    [1, "#f00"],
  ].forEach(([offset, color]) => hue.addColorStop(offset, color));
  context.fillStyle = hue;
  context.fillRect(0, 0, width, height);
}
