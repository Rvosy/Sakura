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
