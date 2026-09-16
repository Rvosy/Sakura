import assert from 'node:assert/strict';
import test from 'node:test';
import { createComposerActionIndicator, createVoiceActionIndicator } from '../chat/composer-action-indicator.js';

function fixture(create = createComposerActionIndicator) {
  const frames = [], accents = [], listeners = new Map(), timers = new Map();
  let timerId = 0, disposed = false, focusVisible = false;
  const button = {
    querySelector: () => ({}),
    addEventListener: (name, listener) => listeners.set(name, listener),
    removeEventListener: name => listeners.delete(name),
    matches: () => focusVisible,
  };
  const controller = create({
    button,
    createIcon: ({ initial }) => {
      frames.push([initial, false]);
      return { set: (name, spin = false) => frames.push([name, spin]), accent: name => accents.push(name), dispose: () => { disposed = true; } };
    },
    setTimer(callback) { timers.set(++timerId, callback); return timerId; },
    clearTimer: id => timers.delete(id),
  });
  return {
    controller, frames, accents, timers, listeners,
    emit: name => listeners.get(name)?.(),
    focus() { focusVisible = true; listeners.get('focus')?.(); },
    disposed: () => disposed,
  };
}

test('send starts once, preserves its loading ring under the pointer, and morphs to stop on re-entry or keyboard focus', () => {
  const f = fixture();
  f.controller.setBusy(true);
  f.controller.setBusy(true);
  f.emit('pointerenter');
  assert.deepEqual(f.frames.at(-1), ['loader-circle', true]);
  assert.deepEqual(f.accents, ['send']);
  f.emit('pointerleave'); f.emit('pointerenter');
  assert.deepEqual(f.frames.at(-1), ['x', false]);
  f.emit('pointerleave');
  assert.deepEqual(f.frames.at(-1), ['loader-circle', true]);
  f.focus();
  assert.deepEqual(f.frames.at(-1), ['x', false]);
  f.emit('blur');
  assert.deepEqual(f.frames.at(-1), ['loader-circle', true]);
  f.controller.dispose();
  assert.equal(f.listeners.size, 0);
  assert.equal(f.disposed(), true);
});

test('cancel restores send immediately; only an accepted success shows a check', () => {
  const f = fixture();
  f.controller.setBusy(true); f.controller.setBusy(false);
  assert.deepEqual(f.frames.at(-1), ['send-horizontal', false]);
  assert.equal(f.frames.some(([name]) => name === 'check'), false);
  f.controller.setBusy(true); f.controller.setBusy(false); f.controller.complete();
  f.controller.setBusy(false); // Ordinary subtitle renders do not dismiss the short success feedback.
  assert.deepEqual(f.frames.at(-1), ['check', false]);
  f.timers.values().next().value();
  assert.deepEqual(f.frames.at(-1), ['send-horizontal', false]);
  f.controller.dispose();
});

test('new sends, generation reset and disposal invalidate stale completion feedback', () => {
  const f = fixture();
  f.controller.complete();
  const stale = f.timers.values().next().value;
  f.controller.setBusy(true);
  stale();
  assert.deepEqual(f.frames.at(-1), ['loader-circle', true]);
  f.controller.reset();
  assert.deepEqual(f.frames.at(-1), ['send-horizontal', false]);
  f.controller.complete();
  const disposedCallback = f.timers.values().next().value;
  f.controller.dispose();
  const count = f.frames.length;
  disposedCallback(); f.controller.setBusy(true); f.controller.complete();
  assert.equal(f.frames.length, count);
});

test('voice follows capture states without a success check on cancellation or failure', () => {
  const f = fixture(createVoiceActionIndicator);
  f.controller.setState('preparing');
  assert.deepEqual(f.frames.at(-1), ['loader-circle', true]);
  f.controller.setState('recording');
  assert.deepEqual(f.frames.at(-1), ['stop', false]);
  f.controller.setState('recognizing');
  assert.deepEqual(f.frames.at(-1), ['loader-circle', true]);
  f.controller.complete(); // A result must have returned the consumer to idle first.
  assert.equal(f.frames.some(([name]) => name === 'check'), false);
  f.controller.setState('idle');
  assert.deepEqual(f.frames.at(-1), ['mic', false]);
  assert.equal(f.frames.some(([name]) => name === 'check'), false);
  f.controller.dispose();
});

test('transcript feedback does not delay another recording and cannot outlive disposal', () => {
  const f = fixture(createVoiceActionIndicator);
  f.controller.complete();
  assert.deepEqual(f.frames.at(-1), ['check', false]);
  const stale = f.timers.values().next().value;
  f.controller.setState('preparing'); stale();
  assert.deepEqual(f.frames.at(-1), ['loader-circle', true]);
  f.controller.reset();
  f.controller.complete();
  const callback = f.timers.values().next().value;
  f.controller.dispose();
  const count = f.frames.length;
  callback(); f.controller.setState('recording'); f.controller.complete();
  assert.equal(f.frames.length, count);
});
