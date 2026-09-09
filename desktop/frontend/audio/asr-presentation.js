import { createVoiceActionIndicator } from '../chat/composer-action-indicator.js';

export function createAsrPresentation({ composer, input, button, status, recording }) {
  const indicator = createVoiceActionIndicator({ button });
  function setState(state) {
    const busy = state !== 'idle';
    const waiting = state === 'preparing' || state === 'recognizing';
    composer.dataset.voiceState = state;
    composer.dataset.voiceActive = String(busy);
    input.readOnly = busy;
    input.inert = busy;
    input.setAttribute('aria-hidden', String(busy));
    status.setAttribute('aria-hidden', String(!waiting));
    // Retain outgoing text during its fade; it is already hidden from accessibility.
    if (waiting) status.textContent = state === 'preparing' ? '正在准备' : '正在识别';
    recording.setAttribute('aria-hidden', String(state !== 'recording'));
    const label = state === 'recording' ? '结束录音并识别' : waiting ? status.textContent : '开始语音输入';
    button.setAttribute('aria-label', label);
    button.dataset.tooltip = label;
    indicator.setState(state);
  }
  return Object.freeze({
    setState,
    complete: () => indicator.complete(),
    reset() { setState('idle'); indicator.reset(); },
    dispose: () => indicator.dispose(),
  });
}
