import { FALLBACK_THEME_TOKENS } from '../core/theme.js';
import { createSnapshot } from './fixtures.js';
import { viewerCopyText } from '../runtime-log/runtime-log-presentation.js';
const query = new URLSearchParams(location.search);
const mode = query.get('mode') === 'proposed' ? 'proposed' : 'current';
const scenario = query.get('scenario') || 'import';
const snapshot = createSnapshot(mode, scenario);

// The production viewer runs unchanged against an in-memory Tauri stand-in.
window.__TAURI__ = {
  core: { invoke: async command => {
    if (command === 'runtime_log_viewer_bootstrap') return {schemaVersion:3, themeTokens:FALLBACK_THEME_TOKENS, snapshot:structuredClone(snapshot)};
    if (command === 'runtime_log_viewer_snapshot') return {...snapshot, resetRequired:false, records:[]};
    if (command === 'close_runtime_log_viewer') { parent.postMessage({type:'demo-close'},location.origin); return; }
    if (command === 'reveal_runtime_log_viewer') return;
    throw new Error(`Unsupported demo command: ${command}`);
  } },
  event: { listen:async () => () => {} },
};

if (mode === 'current') document.querySelector('link[rel="stylesheet"]').href = './baseline.css';

// Preview the exact copy payload without requiring clipboard access in the iframe.
document.querySelector('#copy').addEventListener('click', event => {
  event.stopImmediatePropagation();
  const selected = document.querySelector('.log-record[aria-selected="true"]')?.viewerItem;
  const text = mode === 'proposed' && selected
    ? viewerCopyText({...selected, record:{...selected.record, description:undefined}})
    : event.currentTarget.dataset.copyText;
  if (text) parent.postMessage({type:'demo-copy',text},location.origin);
}, true);

await import(mode === 'proposed' ? '../runtime-log/runtime-log.js' : './baseline-viewer.js');
if (scenario === 'tts') document.querySelector('#tab-tts').click();
if (scenario === 'plugins') document.querySelector('#tab-plugins').click();
