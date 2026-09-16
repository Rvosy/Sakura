import { createMorph } from '../vendor/morphicons/dom.js';
import { icons } from './morph-icon-data.js';

const EASE = 'cubic-bezier(.22, 1, .36, 1)';
const SVG_NS = 'http://www.w3.org/2000/svg';

// One persistent SVG; state changes interrupt the current shape instead of replacing layers.
// Sakura motion is authored by the app, independent of the OS animation preference.
export function createMorphIcon({ element, initial }) {
  const doc = element.ownerDocument;
  const win = doc.defaultView;
  const svg = doc.createElementNS(SVG_NS, 'svg');
  for (const [key, value] of Object.entries({
    viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor',
    'stroke-width': '2', 'stroke-linecap': 'round', 'stroke-linejoin': 'round',
    'aria-hidden': 'true', focusable: 'false',
  })) svg.setAttribute(key, value);
  const path = doc.createElementNS(SVG_NS, 'path');
  svg.append(path);
  const morph = createMorph(path, icons[initial], { reducedMotion: 'never' });
  element.replaceChildren(svg);
  element.dataset.icon = initial;
  let name = initial;
  let spinning = false;
  let spinAnimation = null;
  let settleAnimation = null;
  let accentAnimation = null;
  let spinTimer = null;
  let revision = 0;
  let disposed = false;

  function stopSpin({ settle = true } = {}) {
    revision++;
    win.clearTimeout(spinTimer);
    spinTimer = null;
    const matrix = new win.DOMMatrix(win.getComputedStyle(svg).transform);
    const angle = Math.atan2(matrix.b, matrix.a) * 180 / Math.PI;
    spinAnimation?.cancel();
    settleAnimation?.cancel();
    spinAnimation = null;
    settleAnimation = null;
    if (settle && Math.abs(angle) > .1) {
      settleAnimation = svg.animate([
        { transform: `rotate(${angle}deg)` }, { transform: 'rotate(0deg)' },
      ], { duration: 180, easing: EASE });
    }
  }
  return Object.freeze({
    set(next, spin = false) {
      if (disposed || (name === next && spinning === spin)) return;
      if (!icons[next]) throw new Error(`Unknown morph icon: ${next}`);
      stopSpin();
      name = next;
      spinning = spin;
      element.dataset.icon = next;
      morph.morphTo(icons[next], 'snappy');
      if (spin) {
        const current = revision;
        spinTimer = win.setTimeout(() => {
          spinTimer = null;
          if (disposed || revision !== current || !spinning) return;
          settleAnimation?.cancel();
          spinAnimation = svg.animate([
            { transform: 'rotate(0deg)' }, { transform: 'rotate(360deg)' },
          ], { duration: 900, iterations: Infinity, easing: 'linear' });
        }, 180);
      }
    },
    accent(kind) {
      if (disposed) return;
      accentAnimation?.cancel();
      accentAnimation = element.animate(kind === 'send' ? [
        { transform: 'translate(0, 0) scale(1)' },
        { transform: 'translate(-2px, 0) scale(.94)', offset: .18 },
        { transform: 'translate(5px, -2px) scale(.83)', offset: .55 },
        { transform: 'translate(0, 0) scale(1)' },
      ] : [{ transform: 'scale(.85)' }, { transform: 'scale(1)' }], { duration: 340, easing: EASE });
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      stopSpin({ settle: false });
      accentAnimation?.cancel();
      morph.destroy();
    },
  });
}
