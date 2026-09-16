// Isolated visual prototype: no microphone, backend, storage, or real messages.
import { createMorph } from '../../vendor/morphicons/dom.js';
import { icons } from '../../core/morph-icon-data.js';

const $ = id => document.getElementById(id);
const composer = $('composer');
const draft = $('draft');
const speed = () => $('slow').checked ? 2.5 : 1;
const timers = new Set();
let epoch = 0;
let voice = 'idle';
let chat = 'idle';
let attachment = false;
let caret = { start: 0, end: 0 };
let waveTimer = null;
let hoveredStop = false;
let hoverArmed = false;
let failureNext = false;
let autoRecording = false;
let currentDemo = '';
let layoutAnimations = [];

function later(fn, ms) {
  const version = epoch;
  const timer = setTimeout(() => { timers.delete(timer); if (epoch === version) fn(); }, ms * speed());
  timers.add(timer);
}
function clearFlow() {
  epoch++;
  for (const timer of timers) clearTimeout(timer);
  timers.clear();
  clearInterval(waveTimer);
  waveTimer = null;
}
function animate(el, frames, duration, extra = {}) {
  return el.animate(frames, { duration: duration * speed(), easing: 'cubic-bezier(.22, 1, .36, 1)', ...extra });
}

function motionIcon(button, initial) {
  const svg = button.querySelector('svg');
  const stage = button.querySelector('.icon-stage');
  const path = svg.querySelector('path');
  const morph = createMorph(path, icons[initial], { reducedMotion: 'never' });
  let name = initial;
  let spinning = false;
  let spinAnimation = null;
  let settleAnimation = null;
  let accentAnimation = null;
  let spinTimer = null;
  button.dataset.icon = initial;
  function stopSpin() {
    clearTimeout(spinTimer);
    spinTimer = null;
    const matrix = new DOMMatrix(getComputedStyle(svg).transform);
    const angle = Math.atan2(matrix.b, matrix.a) * 180 / Math.PI;
    spinAnimation?.cancel();
    settleAnimation?.cancel();
    spinAnimation = null;
    settleAnimation = null;
    if (Math.abs(angle) > .1) {
      settleAnimation = animate(svg, [{ transform: `rotate(${angle}deg)` }, { transform: 'rotate(0deg)' }], 200);
    }
  }
  function startSpin() {
    clearTimeout(spinTimer);
    spinTimer = setTimeout(() => {
      spinTimer = null;
      if (!spinning) return;
      settleAnimation?.cancel();
      spinAnimation = svg.animate([{ transform: 'rotate(0deg)' }, { transform: 'rotate(360deg)' }], { duration: 900 * speed(), iterations: Infinity, easing: 'linear' });
    }, 180 * speed());
  }
  function set(next, spin = false, force = false) {
    if (!force && name === next && spinning === spin) return;
    stopSpin();
    name = next;
    spinning = spin;
    button.dataset.icon = next;
    morph.morphTo(icons[next], { stiffness: 420 / speed() ** 2, damping: 30 / speed() });
    if (spin) startSpin();
  }
  return {
    set,
    accent(kind) {
      accentAnimation?.cancel();
      accentAnimation = animate(stage, kind === 'send' ? [
        { transform: 'translate(0, 0) scale(1)' },
        { transform: 'translate(-2px, 0) scale(.94)', offset: .18 },
        { transform: 'translate(5px, -2px) scale(.83)', offset: .55 },
        { transform: 'translate(0, 0) scale(1)' },
      ] : [{ transform: 'scale(.85)' }, { transform: 'scale(1)' }], 340);
    },
    refresh() { accentAnimation?.cancel(); set(name, spinning, true); },
    destroy() { spinning = false; stopSpin(); settleAnimation?.cancel(); accentAnimation?.cancel(); morph.destroy(); },
  };
}
const voiceIcon = motionIcon($('voice'), 'mic');
const sendIcon = motionIcon($('send'), 'send-horizontal');
const bars = Array.from({ length: 48 }, () => { const bar = document.createElement('i'); $('waveform').append(bar); return bar; });

function setFeedback(text = '', retry = false) {
  $('feedback-text').textContent = text;
  $('feedback').dataset.visible = String(Boolean(text));
  $('retry').hidden = !retry;
}
function tools(open = false) {
  $('attachment').setAttribute('aria-expanded', String(open));
  $('tool-menu').dataset.open = String(open);
  $('tool-menu').inert = !open;
}
const voiceBusy = () => ['preparing', 'recording', 'recognizing'].includes(voice);
function layout() {
  const targets = [...composer.querySelectorAll('.action')];
  const before = targets.map(el => el.getBoundingClientRect());
  const oldHeight = composer.getBoundingClientRect().height;
  for (const animation of layoutAnimations) animation?.cancel();
  layoutAnimations = [];
  composer.style.height = '';
  composer.dataset.expanded = 'false';
  draft.style.height = '40px';
  const expanded = !voiceBusy() && (attachment || draft.scrollHeight > 43);
  composer.dataset.expanded = String(expanded);
  draft.style.height = 'auto';
  const textHeight = expanded ? Math.max(40, Math.min(96, draft.scrollHeight)) : 40;
  draft.style.height = '100%';
  composer.style.setProperty('--editor-height', `${textHeight}px`);
  $('attachment-chip').hidden = !attachment || voiceBusy();
  const newHeight = composer.getBoundingClientRect().height;
  composer.style.height = `${newHeight}px`;
  if (Math.abs(oldHeight - newHeight) > 1) {
    layoutAnimations.push(animate(composer, [{ height: `${oldHeight}px` }, { height: `${newHeight}px` }], 260));
    targets.forEach((el, i) => {
      const after = el.getBoundingClientRect();
      layoutAnimations.push(animate(el, [{ transform: `translate(${before[i].x - after.x}px, ${before[i].y - after.y}px)` }, { transform: 'translate(0, 0)' }], 260));
    });
  }
}
function updateSend() {
  if (chat === 'busy') sendIcon.set(hoveredStop ? 'x' : 'loader-circle', !hoveredStop);
  else sendIcon.set(chat === 'complete' ? 'check' : 'send-horizontal');
}
function render() {
  composer.dataset.voice = voice;
  composer.dataset.chat = chat;
  const busy = voiceBusy();
  draft.readOnly = busy;
  draft.inert = busy;
  draft.setAttribute('aria-hidden', String(busy));
  $('attachment').dataset.cancel = String(busy);
  $('attachment').disabled = chat === 'busy';
  $('attachment').setAttribute('aria-label', busy ? '取消语音输入' : '添加附件');
  $('voice').disabled = ['preparing', 'recognizing'].includes(voice) || chat === 'busy';
  $('voice').setAttribute('aria-label', voice === 'recording' ? '结束录音并识别' : voice === 'preparing' ? '正在准备' : voice === 'recognizing' ? '正在识别' : '开始语音输入');
  $('send').disabled = busy || (chat !== 'busy' && !draft.value.trim() && !attachment);
  $('send').setAttribute('aria-label', chat === 'busy' ? '停止回复' : '发送消息');
  $('send').title = chat === 'busy' ? '停止回复' : '发送 · Enter';
  const labels = { preparing: '正在准备', recording: '正在录音', recognizing: '正在识别' };
  const nextLabel = labels[voice] || '';
  if ($('voice-label').textContent !== nextLabel) {
    $('voice-label').textContent = nextLabel;
    if (nextLabel && voice !== 'recording') animate($('voice-label'), [{ opacity: 0, transform: 'translateY(3px)' }, { opacity: 1, transform: 'translateY(0)' }], 180);
  }
  voiceIcon.set(voice === 'recording' ? 'stop' : ['preparing', 'recognizing'].includes(voice) ? 'loader-circle' : voice === 'complete' ? 'check' : 'mic', ['preparing', 'recognizing'].includes(voice));
  updateSend();
  document.querySelectorAll('[data-step]').forEach(el => { el.dataset.active = String(el.dataset.step === voice); });
  const status = { preparing: '正在准备语音输入 · 左侧 × 可取消', recording: '模拟录音中 · 再点方块结束，Esc 取消', recognizing: '正在识别 · 草稿与附件保留', complete: '文字已回填 · 可以编辑后发送', error: '识别失败 · 草稿保留，可直接重试' };
  $('scene-status').textContent = status[voice] || (chat === 'busy' ? '正在等待回复 · 点击右侧按钮可随时停止' : '可以输入，也可以点击麦克风');
  layout();
}
function focusDraft() { draft.focus({ preventScroll: true }); draft.setSelectionRange(caret.start, caret.end); }
function beginWaves() {
  clearInterval(waveTimer);
  let t = 0;
  const history = Array(bars.length).fill(.1);
  waveTimer = setInterval(() => {
    if (voice !== 'recording') return;
    t += .1;
    const envelope = Math.sin(t * .85) > -.55 ? .12 + Math.abs(Math.sin(t * 6.8) * Math.cos(t * 2.4)) * .8 : .09;
    history.shift(); history.push(envelope);
    bars.forEach((bar, i) => { bar.style.transform = `scaleY(${history[i]})`; });
  }, 100 * speed());
}
function startVoice({ automatic = false, fail = false } = {}) {
  if (chat === 'busy') return;
  clearFlow(); tools(); setFeedback();
  $('reply').dataset.visible = 'false';
  if (voice !== 'error') caret = { start: draft.selectionStart, end: draft.selectionEnd };
  failureNext = fail;
  autoRecording = automatic;
  voice = 'preparing'; chat = 'idle'; render();
  later(() => {
    voice = 'recording'; render(); beginWaves();
    if (autoRecording) later(recognize, 2300);
  }, 650);
}
function recognize() {
  clearFlow();
  voice = 'recognizing';
  bars.forEach(bar => { bar.style.transform = 'scaleY(.1)'; });
  render();
  later(() => {
    if (failureNext) { voice = 'error'; setFeedback('识别失败，草稿已保留。', true); render(); return; }
    const transcript = '今晚陪我玩点轻松的游戏吧。';
    draft.value = draft.value.slice(0, caret.start) + transcript + draft.value.slice(caret.end);
    caret = { start: caret.start + transcript.length, end: caret.start + transcript.length };
    voice = 'complete'; render(); voiceIcon.accent('complete'); focusDraft();
    later(() => { voice = 'idle'; render(); }, 600);
  }, 1100);
}
function cancelVoice() {
  clearFlow(); voice = 'idle'; setFeedback(); tools(); render(); focusDraft();
}
function sendMessage() {
  if (voiceBusy() || (!draft.value.trim() && !attachment)) return;
  clearFlow(); tools(); setFeedback();
  voice = 'idle'; chat = 'busy'; hoveredStop = false; hoverArmed = false;
  $('reply').dataset.visible = 'false';
  draft.value = ''; attachment = false; caret = { start: 0, end: 0 };
  render(); sendIcon.accent('send');
  later(() => {
    chat = 'complete'; hoveredStop = false; render();
    $('reply-text').textContent = '好呀，今晚就挑一款轻松的，一起慢慢玩。';
    $('reply').dataset.visible = 'true';
    later(() => { chat = 'idle'; render(); }, 600);
  }, 3400);
}
function stopReply() {
  clearFlow(); chat = 'idle'; hoveredStop = false; voice = 'idle';
  setFeedback('已停止回复'); render();
  later(() => setFeedback(), 1800);
}
function reset() {
  clearFlow(); tools(); setFeedback(); voice = 'idle'; chat = 'idle'; attachment = false;
  draft.value = ''; caret = { start: 0, end: 0 }; hoveredStop = false; hoverArmed = false;
  failureNext = false; autoRecording = false; currentDemo = '';
  $('reply').dataset.visible = 'false';
  document.querySelectorAll('[data-demo]').forEach(el => { el.dataset.active = 'false'; });
  render();
}
$('voice').addEventListener('click', () => voice === 'recording' ? recognize() : startVoice());
$('composer').addEventListener('submit', event => { event.preventDefault(); if (!$('send').disabled) chat === 'busy' ? stopReply() : sendMessage(); });
$('send').addEventListener('pointerleave', () => { hoverArmed = true; if (chat === 'busy') { hoveredStop = false; updateSend(); } });
$('send').addEventListener('pointerenter', () => { if (chat === 'busy' && hoverArmed) { hoveredStop = true; updateSend(); } });
$('send').addEventListener('focus', () => { if (chat === 'busy' && $('send').matches(':focus-visible')) { hoveredStop = true; updateSend(); } });
$('send').addEventListener('blur', () => { if (chat === 'busy') { hoveredStop = false; updateSend(); } });
$('attachment').addEventListener('click', () => { if (voiceBusy()) cancelVoice(); else tools($('attachment').getAttribute('aria-expanded') !== 'true'); });
$('add-attachment').addEventListener('click', () => { attachment = true; tools(); render(); });
$('remove-attachment').addEventListener('click', () => { attachment = false; render(); });
$('retry').addEventListener('click', () => startVoice({ automatic: currentDemo === 'error' }));
draft.addEventListener('input', () => { if (voice === 'complete' || voice === 'error') voice = 'idle'; setFeedback(); render(); });
draft.addEventListener('keydown', event => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); if (chat !== 'busy') composer.requestSubmit(); } });
document.addEventListener('keydown', event => { if (event.key === 'Escape') { if (voiceBusy()) cancelVoice(); else if (chat === 'busy') stopReply(); tools(); } });
document.addEventListener('pointerdown', event => { if (!$('tool-menu').contains(event.target) && !$('attachment').contains(event.target)) tools(); });
$('reset').addEventListener('click', reset);
document.querySelectorAll('[data-demo]').forEach(button => button.addEventListener('click', () => {
  reset(); currentDemo = button.dataset.demo; button.dataset.active = 'true';
  if (currentDemo === 'voice' || currentDemo === 'error') {
    if (currentDemo === 'error') { draft.value = '周末想放松一下，'; draft.setSelectionRange(draft.value.length, draft.value.length); }
    startVoice({ automatic: true, fail: currentDemo === 'error' });
  } else {
    draft.value = '帮我挑一款轻松的游戏。'; render();
    later(() => {
      sendMessage();
      if (currentDemo === 'stop') {
        later(() => { hoveredStop = true; updateSend(); }, 1450);
        later(stopReply, 2150);
      }
    }, 650);
  }
}));
function refreshMotion() {
  document.documentElement.style.setProperty('--speed', speed());
  voiceIcon.refresh(); sendIcon.refresh();
  if (voice === 'recording') beginWaves();
}
$('slow').addEventListener('change', refreshMotion);
$('dark').addEventListener('change', () => { document.body.dataset.dark = String($('dark').checked); });
function fitScene() {
  const scene = document.querySelector('.scene');
  const scale = Math.min(1, (scene.clientWidth - 8) / 600);
  scene.style.setProperty('--scene-scale', scale);
  scene.style.height = `${Math.max(440, 656 * scale + 30)}px`;
  scene.style.minHeight = '0';
}
new ResizeObserver(fitScene).observe(document.querySelector('.scene'));
window.addEventListener('pagehide', () => { clearFlow(); voiceIcon.destroy(); sendIcon.destroy(); });
refreshMotion(); render(); fitScene();
