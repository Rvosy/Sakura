import test from 'node:test';
import assert from 'node:assert/strict';
import { requirementSummary } from '../core/plugin-requirements.js';

const genie = { id: 'sakura.tts.genie', name: 'Genie', compatible: true, enabled: true };
const sovits = { id: 'sakura.tts.gpt-sovits', name: 'GPT-SoVITS', compatible: true, enabled: true };
const requirement = { kind: 'tts', type: 'gpt-sovits.models@1', plugins: [{ id: sovits.id, name: sovits.name }] };

test('requirements show the enabled supporting provider even when the package suggests another', () => {
  const summary = requirementSummary({ ...requirement, reasonCode: 'COMPATIBLE', candidates: [genie] });
  assert.equal(summary.state, 'enabled');
  assert.deepEqual(summary.names, ['Genie']);
});

test('requirements list all enabled supporting providers without claiming a selected provider', () => {
  const summary = requirementSummary({ ...requirement, reasonCode: 'COMPATIBLE', candidates: [
    genie, sovits, { id: 'disabled', compatible: true, enabled: false }, { id: 'unsupported', compatible: false, enabled: true },
  ] });
  assert.deepEqual(summary.names, ['Genie', 'GPT-SoVITS']);
});

test('unavailable requirements distinguish installation, enablement and version support', () => {
  for (const [reasonCode, candidates, state, names] of [
    ['PLUGIN_MISSING', [], 'missing', ['GPT-SoVITS']],
    ['PLUGIN_DISABLED', [{ ...genie, enabled: false }], 'disabled', ['Genie']],
    ['PLUGIN_INCOMPATIBLE', [{ ...genie, compatible: false }], 'unsupported', ['Genie']],
  ]) {
    const summary = requirementSummary({ ...requirement, reasonCode, candidates });
    assert.equal(summary.state, state);
    assert.deepEqual(summary.names, names);
  }
});
