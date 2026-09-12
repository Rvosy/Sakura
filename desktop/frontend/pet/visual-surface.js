import { constrainedPortraitScale } from "./appearance.js";
import { computeHitRegions } from "./hit-regions.js";

export function applyVisualSurfaceAppearance(stage, layout, surface, scalePercent) {
  const sourceSize = [surface.width, surface.height];
  const [, , slotWidth, slotHeight] = layout.portraitRect;
  const contain = Math.min(slotWidth / surface.width, slotHeight / surface.height);
  const scale = constrainedPortraitScale({
    requestedPercent: scalePercent,
    sourceSize,
    portraitRect: layout.portraitRect,
    windowSize: layout.windowSize,
  });
  stage.style.setProperty("--visual-surface-width", `${surface.width * contain}px`);
  stage.style.setProperty("--visual-surface-height", `${surface.height * contain}px`);
  stage.style.setProperty("--portrait-render-scale", String(scale));
  return computeHitRegions(layout, { portraitSourceSize: sourceSize, portraitScalePercent: scalePercent });
}
