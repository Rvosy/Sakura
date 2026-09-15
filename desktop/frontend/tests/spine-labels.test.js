import test from 'node:test';
import assert from 'node:assert/strict';
import { labelFor } from '../../../plugins/builtin/sakura_spine/editor.mjs';

test('Spine names come only from the resource, with raw IDs for missing or empty labels', () => {
  const ids = ['anger', 'normal', 'sad', 'shy', 'smile', 'surprise', 'unique1', 'unique2'];
  const labels = { anger: '', sad: '心虚', shy: '惊讶', smile: '开心', surprise: '难过', unique1: '害羞', unique2: '生气' };
  assert.deepEqual(ids.map(id => labelFor(id, 'skin', labels)),
    ['anger', 'normal', '心虚', '惊讶', '开心', '难过', '害羞', '生气']);
  assert.equal(labelFor('default', 'skin'), 'default');
  assert.equal(labelFor('smile', 'skin', { smile: '  ' }), 'smile');
  assert.equal(labelFor('idle', 'animation'), 'idle');
  assert.equal(labelFor('attack', 'animation'), 'attack');
});
