// Served as /app.js by the opt-in browser journey; only the native/ASR boundary is simulated.
import { createAsrController } from './audio/asr-controller.js';
import { createAsrPresentation } from './audio/asr-presentation.js';
import { createAsrWaveform } from './audio/asr-waveform.js';
import { createComposerActionIndicator } from './chat/composer-action-indicator.js';
import { createWaitingIndicator } from './chat/waiting-indicator.js';
import { createTypewriter } from './pet/typewriter.js';
const $ = id => document.getElementById(id);
const composer = $('composer'), input = $('composer-input'), button = $('voice-mic');
const root = $('pet-stage');
Object.entries({
  '--stage-width': '900px', '--stage-height': '800px', '--content-scale': '1',
  '--portrait-x': '150px', '--portrait-y': '95px', '--portrait-width': '600px', '--portrait-height': '656px',
  '--input-x': '174px', '--input-y': '611px', '--input-width': '553px', '--input-height': '52px',
  '--bubble-x': '175px', '--bubble-y': '75px', '--bubble-width': '553px', '--bubble-height': '128px',
  '--primary': '#d55b91', '--primary-hover': '#bf3f7a', '--text': '#3d2b35', '--secondary-text': '#7a3656',
}).forEach(([name, value]) => root.style.setProperty(name, value));
document.body.style.background = '#eeeeef';
document.body.dataset.shellState = 'ready';
$('portrait-current').src = './prototypes/asr/assets/navi.png';
$('chat-bubble').style.visibility = 'hidden';
button.hidden = false;
composer.dataset.asrEnabled = 'true';
composer.dataset.inputExpanded = 'false';
const view = createAsrPresentation({ composer, input, button, status: $('voice-status'), recording: $('voice-recording') });
const send = createComposerActionIndicator({ button: $('composer-send') });
const waveform = createAsrWaveform({ canvas: $('voice-waveform'), window });
const writes = [], errors = [];
let version = 0, prepareResolve, pendingPoll, response, context = 'test-generation', recordingId;
function layout() {
  // This host fixture supplies geometry; the real native transaction has its own journey.
  const expanded = input.value.includes('\n');
  composer.dataset.inputExpanded = String(expanded);
  composer.style.setProperty('--input-text-height', `${Math.min(3, input.value.split('\n').length) * parseFloat(getComputedStyle(input).lineHeight)}px`);
  input.style.height = expanded ? composer.style.getPropertyValue('--input-text-height') : '40px';
  input.scrollTop = 0;
  composer.style.height = 'auto';
}

const controller = createAsrController({
  invoke: async (name, args) => {
    recordingId = args.payload.recordingId;
    if (name === 'asr_prepare') return new Promise(resolve => { prepareResolve = resolve; });
    return { recordingId, ...(name === 'asr_poll' ? response : { state: { asr_capture_start: 'recording', asr_capture_stop: 'recognizing', asr_cancel: 'cancelled' }[name] }) };
  },
  listen: async () => () => {},
  readContext: () => context,
  readDraft: () => ({ value: input.value, version, selectionStart: input.selectionStart, selectionEnd: input.selectionEnd }),
  writeDraft: next => { writes.push(next); input.value = next.value; version++; view.complete(); layout(); },
  restoreSelection: saved => { if (saved) input.setSelectionRange(saved.selectionStart, saved.selectionEnd); },
  onState: ({ state }) => {
    view.setState(state);
    button.disabled = state === 'preparing' || state === 'recognizing';
    $('composer-attachment').dataset.action = state === 'idle' ? 'tools' : 'cancel';
    if (state === 'recording') waveform.start(); else waveform.stop();
    layout();
  },
  onError: message => errors.push(message),
  schedule: callback => { pendingPoll = callback; return 1; },
  unschedule: () => { pendingPoll = null; },
});
await controller.connect();
button.addEventListener('click', () => controller.active() ? void controller.stop() : void controller.start());
$('composer-attachment').addEventListener('click', () => void controller.cancel());
input.addEventListener('input', () => { version++; layout(); });
composer.addEventListener('submit', event => { event.preventDefault(); send.setBusy(true); });
const waitingFrames = [], subtitles = [];
const waiting = createWaitingIndicator({ onFrame: frame => waitingFrames.push(frame) });
const writer = createTypewriter({ intervalMs: 40, onText: text => subtitles.push(text) });
window.motionJourney = {
  send, view, writes, errors, waitingFrames, subtitles,
  start: () => { void controller.start(); },
  ready: () => prepareResolve({ state: 'ready', recordingId }),
  stop: () => controller.stop(),
  cancel: () => controller.cancel(),
  poll: async value => { response = value; const callback = pendingPoll; pendingPoll = null; await callback?.(); },
  level: value => waveform.push(value),
  invalidate: () => { context = 'next-generation'; },
  state: () => controller.state(),
  startText() { waiting.start(); writer.start([{ text: '逐字播放仍然继续' }]); },
  dispose() { controller.dispose(); waveform.stop(); view.dispose(); send.dispose(); waiting.stop(); writer.cancel(); },
};
view.setState('idle'); layout();
