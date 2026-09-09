// Only host RMS summaries enter this bounded history. Rendering never clocks audio capture.
export function createAsrWaveform({ canvas, window }) {
  const context = canvas.getContext("2d");
  let running = false;
  let frame = null;
  let bucketAt = null;
  let peak = 0;
  let history = [];
  function draw(now) {
    if (!running || !context) return;
    const interval = 80;
    if (bucketAt === null) bucketAt = now;
    if (now - bucketAt >= interval) {
      // Host levels already use decibels. Expand the useful speech range and leave
      // a quiet baseline; do not flatten syllables with another smoothing pass.
      history.push(Math.pow(Math.max(0, Math.min(1, (peak - 0.16) / 0.60)), 1.25));
      history = history.slice(-160);
      peak = 0;
      bucketAt = now;
    }
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    const ratio = window.devicePixelRatio || 1;
    if (canvas.width !== Math.round(width * ratio) || canvas.height !== Math.round(height * ratio)) {
      canvas.width = Math.round(width * ratio);
      canvas.height = Math.round(height * ratio);
    }
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, width, height);
    context.strokeStyle = window.getComputedStyle(canvas).color;
    context.lineWidth = 3;
    context.lineCap = "round";
    const shift = Math.min(1, (now - bucketAt) / interval) * 6;
    const count = Math.ceil(width / 6) + 1;
    context.beginPath();
    for (let index = 0; index < count; index += 1) {
      const x = width - index * 6 - shift;
      const level = history[history.length - 1 - index] || 0;
      const amplitude = Math.max(0.1, Math.min(1, level) * (height - 5) / 2);
      context.moveTo(x, height / 2 - amplitude);
      context.lineTo(x, height / 2 + amplitude);
    }
    context.stroke();
    frame = window.requestAnimationFrame(draw);
  }
  return Object.freeze({
    push(level) { if (running && Number.isFinite(level)) peak = Math.max(peak, level); },
    start() {
      if (running) return;
      running = true; history = []; peak = 0; bucketAt = null;
      frame = window.requestAnimationFrame(draw);
    },
    stop() { running = false; window.cancelAnimationFrame(frame); frame = null; },
  });
}
