import { createMorphIcon } from '../core/morph-icon.js';

// These controllers only present real action states; they never defer an action or own its result.
export function createComposerActionIndicator({
  button, createIcon = createMorphIcon,
  setTimer = setTimeout, clearTimer = clearTimeout,
}) {
  const icon = createIcon({ element: button.querySelector('.sakura-morph-icon'), initial: 'send-horizontal' });
  let busy = false;
  let hoverArmed = false;
  let stopping = false;
  let completion = null;
  let disposed = false;
  const listeners = [];
  function clearCompletion() { clearTimer(completion); completion = null; }
  function render() {
    icon.set(busy ? (stopping ? 'x' : 'loader-circle') : completion !== null ? 'check' : 'send-horizontal', busy && !stopping);
  }
  function listen(name, handler) { button.addEventListener(name, handler); listeners.push([name, handler]); }
  listen('pointerleave', () => {
    hoverArmed = true;
    if (busy) { stopping = false; render(); }
  });
  listen('pointerenter', () => {
    if (busy && hoverArmed) { stopping = true; render(); }
  });
  listen('focus', () => {
    if (busy && button.matches(':focus-visible')) { stopping = true; render(); }
  });
  listen('blur', () => { if (busy) { stopping = false; render(); } });
  return Object.freeze({
    setBusy(next) {
      if (disposed || busy === Boolean(next)) return;
      busy = Boolean(next);
      clearCompletion();
      stopping = false;
      hoverArmed = false;
      render();
      if (busy) icon.accent('send');
    },
    complete() {
      if (disposed) return;
      busy = false;
      clearCompletion();
      const timer = setTimer(() => {
        if (disposed || completion !== timer) return;
        completion = null;
        render();
      }, 600);
      completion = timer;
      render();
    },
    reset() {
      if (disposed) return;
      busy = false;
      stopping = false;
      hoverArmed = false;
      clearCompletion();
      render();
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      clearCompletion();
      for (const [name, handler] of listeners) button.removeEventListener(name, handler);
      icon.dispose();
    },
  });
}

export function createVoiceActionIndicator({
  button, createIcon = createMorphIcon,
  setTimer = setTimeout, clearTimer = clearTimeout,
}) {
  const icon = createIcon({ element: button.querySelector('.sakura-morph-icon'), initial: 'mic' });
  let state = 'idle';
  let completion = null;
  let disposed = false;
  function clearCompletion() { clearTimer(completion); completion = null; }
  function render() {
    const waiting = state === 'preparing' || state === 'recognizing';
    icon.set(waiting ? 'loader-circle' : state === 'recording' ? 'stop' : completion !== null ? 'check' : 'mic', waiting);
  }
  return Object.freeze({
    setState(next) {
      if (disposed || state === next) return;
      state = next;
      clearCompletion();
      render();
    },
    complete() {
      if (disposed || state !== 'idle') return;
      clearCompletion();
      const timer = setTimer(() => {
        if (disposed || completion !== timer) return;
        completion = null;
        render();
      }, 600);
      completion = timer;
      render();
      icon.accent('complete');
    },
    reset() {
      if (disposed) return;
      state = 'idle';
      clearCompletion();
      render();
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      clearCompletion();
      icon.dispose();
    },
  });
}
