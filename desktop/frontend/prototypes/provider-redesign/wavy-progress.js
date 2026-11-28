/*
 * Material 3 Expressive Indeterminate LinearWavyProgressIndicator (Web/SVG port)
 *
 * 参数与节奏对齐 androidx.compose.material3 (1.5.x):
 *   - LinearAnimationDuration = 1750ms
 *   - FirstLine:  head 0..1000ms, tail 250..1250ms
 *   - SecondLine: head 650..1500ms, tail 900..1750ms
 *   - Easing: EasingEmphasizedAccelerateCubicBezier = cubic-bezier(0.3, 0.0, 0.8, 0.15)
 *   - Wave phase: 1 wavelength (20px) / 1000ms，与 1750ms 主周期形成 7 秒相位差循环
 *   - 几何：交替二次贝塞尔半波 + De Casteljau 精确区间裁剪，配合自适应间隙的圆头背景轨道
 */

const SVG_NS = "http://www.w3.org/2000/svg";

const CYCLE_DURATION_MS = 1750;
const FIRST_HEAD_DELAY = 0;
const FIRST_HEAD_DUR = 1000;
const FIRST_TAIL_DELAY = 250;
const FIRST_TAIL_DUR = 1000;
const SECOND_HEAD_DELAY = 650;
const SECOND_HEAD_DUR = 850;
const SECOND_TAIL_DELAY = 900;
const SECOND_TAIL_DUR = 850;

// 启动时跳过前 220ms 空白爬坡，点击即刻看到第一段波形从左侧生长
const INITIAL_CYCLE_OFFSET_MS = 220;
const WAVE_PERIOD_MS = 1000;
const AMPLITUDE_RAMP_MS = 420;
const CONVERGE_DURATION_MS = 360;

const HEIGHT = 14;
const CENTER_Y = HEIGHT / 2;
const STROKE_WIDTH = 2.1;
const CAP_INSET = STROKE_WIDTH / 2;
const WAVELENGTH = 20;
const HALF_WAVE = WAVELENGTH / 2;
const MAX_AMPLITUDE = 3.2;
const GAP_SIZE = 4.0;

function clamp(value, min, max) {
  return value < min ? min : value > max ? max : value;
}

function lerp(a, b, t) {
  return a + (b - a) * t;
}

function solveCubicBezier(p1x, p1y, p2x, p2y, xTarget) {
  if (xTarget <= 0) return 0;
  if (xTarget >= 1) return 1;
  let lo = 0;
  let hi = 1;
  for (let i = 0; i < 16; i += 1) {
    const u = (lo + hi) * 0.5;
    const inv = 1 - u;
    const x = 3 * inv * inv * u * p1x + 3 * inv * u * u * p2x + u * u * u;
    if (x < xTarget) lo = u;
    else hi = u;
  }
  const u = (lo + hi) * 0.5;
  const inv = 1 - u;
  return 3 * inv * inv * u * p1y + 3 * inv * u * u * p2y + u * u * u;
}

// MotionTokens.EasingEmphasizedAccelerateCubicBezier
function easeEmphasizedAccelerate(t) {
  return solveCubicBezier(0.3, 0.0, 0.8, 0.15, t);
}

// MotionTokens.EasingStandardCubicBezier
function easeStandard(t) {
  return solveCubicBezier(0.2, 0.0, 0.0, 1.0, t);
}

function evalKeyframe(cycleMs, delay, duration) {
  if (cycleMs <= delay) return 0;
  if (cycleMs >= delay + duration) return 1;
  return easeEmphasizedAccelerate((cycleMs - delay) / duration);
}

function evalIndeterminateFractions(cycleMs) {
  return [
    [evalKeyframe(cycleMs, FIRST_TAIL_DELAY, FIRST_TAIL_DUR), evalKeyframe(cycleMs, FIRST_HEAD_DELAY, FIRST_HEAD_DUR)],
    [evalKeyframe(cycleMs, SECOND_TAIL_DELAY, SECOND_TAIL_DUR), evalKeyframe(cycleMs, SECOND_HEAD_DELAY, SECOND_HEAD_DUR)],
  ];
}

function fmt(n) {
  return Number(n.toFixed(2));
}

/**
 * 在屏幕区间 [clipStart, clipEnd] 内生成与 AndroidX LinearWavyProgressModifiers 等价的
 * 精确二次贝塞尔波浪 SVG path（用 Blossom 公式裁剪首尾半波，无折线离散感）。
 */
function buildWaveSegmentPath(clipStart, clipEnd, waveShift, amplitudePx) {
  if (clipEnd - clipStart <= 0.15) return "";
  if (amplitudePx <= 0.05) {
    return `M ${fmt(clipStart)} ${CENTER_Y} L ${fmt(clipEnd)} ${CENTER_Y}`;
  }

  // 与 AndroidX 相同：u = x + waveShift 映射到全长波浪路径，因此 waveShift 增大时波峰向左流动
  const uStart = clipStart + waveShift;
  const uEnd = clipEnd + waveShift;
  const firstHalfIdx = Math.floor(uStart / HALF_WAVE);
  const lastHalfIdx = Math.floor((uEnd - 1e-4) / HALF_WAVE);

  // 控制点偏移量为峰值的 2 倍（二次贝塞尔 B(0.5) = 0.5 * controlOffset = ±amplitudePx）
  const controlOffset = amplitudePx * 2;
  let d = "";

  for (let idx = firstHalfIdx; idx <= lastHalfIdx; idx += 1) {
    const segU0 = idx * HALF_WAVE;
    const segU1 = segU0 + HALF_WAVE;
    const t0 = clamp((uStart - segU0) / HALF_WAVE, 0, 1);
    const t1 = clamp((uEnd - segU0) / HALF_WAVE, 0, 1);
    if (t1 - t0 <= 1e-4) continue;

    const sign = idx % 2 === 0 ? 1 : -1;
    const cy = sign * controlOffset;

    const p0x = segU0 - waveShift;
    const p2x = segU1 - waveShift;
    const p1x = (p0x + p2x) * 0.5;

    const q0x = lerp(p0x, p2x, t0);
    const q0y = CENTER_Y + 2 * t0 * (1 - t0) * cy;

    const q2x = lerp(p0x, p2x, t1);
    const q2y = CENTER_Y + 2 * t1 * (1 - t1) * cy;

    // 二次贝塞尔 Blossom 极点：B(t0, t1)（水平方向线性，x 即中点）
    const q1x = (q0x + q2x) * 0.5;
    const q1y = CENTER_Y + (t0 * (1 - t1) + (1 - t0) * t1) * cy;

    if (!d) {
      d = `M ${fmt(q0x)} ${fmt(q0y)}`;
    }
    d += ` Q ${fmt(q1x)} ${fmt(q1y)} ${fmt(q2x)} ${fmt(q2y)}`;
  }
  return d;
}

function buildTrackPaths(width, segments, trackStartX) {
  const minX = Math.max(CAP_INSET, trackStartX);
  const maxX = width - CAP_INSET;
  if (maxX - minX <= 0.5) return [];

  const paths = [];
  let nextEnd = maxX;

  for (let i = 0; i < segments.length; i += 1) {
    const [tailFrac, headFrac] = segments[i];
    const barTail = tailFrac * width;
    const barHead = headFrac * width;
    if (barHead <= barTail + 0.25) continue;

    const activeVisible = barHead >= CAP_INSET;
    const adjustedGap = activeVisible ? Math.min(barHead - CAP_INSET, GAP_SIZE) : 0;
    const spacing = activeVisible ? adjustedGap + STROKE_WIDTH : adjustedGap;

    const adjustedHead = clamp(barHead, CAP_INSET, maxX);
    const adjustedTail = clamp(barTail, CAP_INSET, maxX);

    const rightSegStart = Math.max(minX, adjustedHead + spacing);
    if (nextEnd > rightSegStart + 0.5) {
      paths.push(`M ${fmt(rightSegStart)} ${CENTER_Y} L ${fmt(nextEnd)} ${CENTER_Y}`);
    }
    nextEnd = Math.min(nextEnd, Math.max(minX, adjustedTail - spacing));
  }

  if (nextEnd > minX + 0.5) {
    paths.push(`M ${fmt(minX)} ${CENTER_Y} L ${fmt(nextEnd)} ${CENTER_Y}`);
  }
  return paths;
}

export function createLinearWavyProgress(container, { startedAt = performance.now() } = {}) {
  const doc = container.ownerDocument;
  const win = doc.defaultView || window;

  const svg = doc.createElementNS(SVG_NS, "svg");
  svg.setAttribute("class", "provider-wavy-svg");
  svg.setAttribute("height", String(HEIGHT));
  svg.setAttribute("preserveAspectRatio", "none");
  svg.setAttribute("aria-hidden", "true");

  const makePath = (cls) => {
    const path = doc.createElementNS(SVG_NS, "path");
    path.setAttribute("class", cls);
    path.setAttribute("fill", "none");
    path.setAttribute("stroke-linecap", "round");
    path.setAttribute("stroke-width", String(STROKE_WIDTH));
    svg.append(path);
    return path;
  };

  const trackEls = [makePath("provider-wavy-track"), makePath("provider-wavy-track"), makePath("provider-wavy-track")];
  const waveEls = [makePath("provider-wavy-active"), makePath("provider-wavy-active")];

  container.replaceChildren(svg);

  let width = 120;
  let disposed = false;
  let rafId = 0;
  let convergeState = null;

  const updateWidth = () => {
    const measured = container.getBoundingClientRect().width || container.clientWidth;
    if (measured > 12) {
      width = measured;
      svg.setAttribute("viewBox", `0 0 ${fmt(width)} ${HEIGHT}`);
    }
  };
  updateWidth();

  let resizeObserver = null;
  if (typeof win.ResizeObserver === "function") {
    resizeObserver = new win.ResizeObserver(() => {
      if (!disposed) updateWidth();
    });
    resizeObserver.observe(container);
  }

  let latestSnapshot = {
    segments: evalIndeterminateFractions(INITIAL_CYCLE_OFFSET_MS),
    amplitude: 0,
    waveShift: 0,
  };

  function renderFrame(now) {
    if (disposed) return;
    if (width <= 12) updateWidth();

    let segments;
    let amplitudePx;
    let waveShift;
    let trackStartX = CAP_INSET;

    if (!convergeState) {
      const elapsed = Math.max(0, now - startedAt);
      const cycleMs = (elapsed + INITIAL_CYCLE_OFFSET_MS) % CYCLE_DURATION_MS;
      const ampProgress = clamp(elapsed / AMPLITUDE_RAMP_MS, 0, 1);
      const amplitude = easeStandard(ampProgress);
      amplitudePx = amplitude * MAX_AMPLITUDE;
      waveShift = ((elapsed % WAVE_PERIOD_MS) / WAVE_PERIOD_MS) * WAVELENGTH;
      segments = evalIndeterminateFractions(cycleMs);

      latestSnapshot = { segments, amplitude, waveShift, cycleAt: now };
    } else {
      const dt = Math.max(0, now - convergeState.startTime);
      const rawP = clamp(dt / CONVERGE_DURATION_MS, 0, 1);
      const headEase = easeStandard(clamp(rawP * 1.18, 0, 1));
      const tailEase = easeEmphasizedAccelerate(rawP);
      const elapsedSinceStart = Math.max(0, now - startedAt);
      waveShift = ((elapsedSinceStart % WAVE_PERIOD_MS) / WAVE_PERIOD_MS) * WAVELENGTH;

      segments = convergeState.segments.map(([t0, h0]) => [
        lerp(t0, 1, tailEase),
        lerp(h0, 1, headEase),
      ]);
      amplitudePx = convergeState.amplitude * MAX_AMPLITUDE * (1 - rawP * 0.9);
      trackStartX = lerp(CAP_INSET, width - CAP_INSET, headEase);

      if (!convergeState.revealed && rawP >= 0.45) {
        convergeState.revealed = true;
        convergeState.onRevealCheck?.();
      }
      if (rawP >= 1) {
        const done = convergeState.onComplete;
        dispose();
        done?.();
        return;
      }
    }

    const trackPaths = buildTrackPaths(width, segments, trackStartX);
    for (let i = 0; i < trackEls.length; i += 1) {
      trackEls[i].setAttribute("d", trackPaths[i] || "");
    }

    const maxX = width - CAP_INSET;
    for (let i = 0; i < waveEls.length; i += 1) {
      const seg = segments[i];
      if (!seg || seg[1] <= seg[0] + 0.002) {
        waveEls[i].setAttribute("d", "");
        continue;
      }
      const clipStart = clamp(seg[0] * width, CAP_INSET, maxX);
      const clipEnd = clamp(seg[1] * width, CAP_INSET, maxX);
      waveEls[i].setAttribute("d", buildWaveSegmentPath(clipStart, clipEnd, waveShift, amplitudePx));
    }

    rafId = win.requestAnimationFrame(renderFrame);
  }

  rafId = win.requestAnimationFrame(renderFrame);

  function converge({ onRevealCheck, onComplete } = {}) {
    if (disposed || convergeState) return;

    // 如果测试恰好在分段交替间隙完成，构造一段稳妥可见的波段向右收敛，避免空白闪烁
    const visible = latestSnapshot.segments.filter(([t, h]) => h - t >= 0.08);
    const seedSegments = visible.length
      ? visible
      : [[0.22, 0.78]];

    convergeState = {
      startTime: win.performance.now(),
      segments: seedSegments,
      amplitude: Math.max(0.45, latestSnapshot.amplitude),
      revealed: false,
      onRevealCheck,
      onComplete,
    };
  }

  function dispose() {
    if (disposed) return;
    disposed = true;
    if (rafId) {
      win.cancelAnimationFrame(rafId);
      rafId = 0;
    }
    resizeObserver?.disconnect();
    resizeObserver = null;
  }

  return Object.freeze({ converge, dispose });
}
