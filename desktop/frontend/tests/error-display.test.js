import test from 'node:test';
import assert from 'node:assert/strict';
import { errorText } from '../core/error-display.js';

test('structured boundary failures retain source path, chain and traceback while redacting credentials', () => {
  const result = errorText({code: 'LEGACY_RUNTIME_UNAVAILABLE', message: '启动失败', details: {diagnostics: {
    diagnostic: 'OSError: C:\\旧目录\\python.exe win32=5 api_key=private-value',
    exception_chain: 'PermissionError: access denied',
    exception_stack: 'Traceback\n  File "bootstrap.py", line 12',
  }}});
  for (const detail of ['LEGACY_RUNTIME_UNAVAILABLE', 'C:\\旧目录\\python.exe', 'win32=5', 'PermissionError: access denied', 'Traceback\n']) assert.ok(result.includes(detail));
  assert.ok(!result.includes('private-value'));
});

test('native Error causes and stacks survive display without looping on cyclic causes', () => {
  const cause = new Error('connection reset');
  const error = new Error('request failed', {cause});
  cause.cause = error;
  const result = errorText(error);
  assert.ok(result.includes('connection reset'));
  assert.ok(result.includes('error-display.test.js'));
});

test('Core routing envelopes are readable without losing pipes or lines in the original failure', () => {
  const diagnostic = 'PermissionError: C:\\角色\\model.json\nexpected read | write\n  at load_model:42';
  const result = errorText(`SETTINGS_LOAD_FAILED|voice|provider|${diagnostic}`);
  assert.ok(result.startsWith(diagnostic));
  for (const value of ['SETTINGS_LOAD_FAILED', 'voice', 'provider']) assert.ok(result.includes(value));
  assert.ok(!result.includes('SETTINGS_LOAD_FAILED|voice|provider|'));
  assert.equal(errorText(result), result);
});

test('dialog details retain the same exception context as runtime logs and the tail of long traces', () => {
  const trace = 'Traceback\n' + '  at provider.py:42\n'.repeat(1000) + 'ROOT_CAUSE_AT_END';
  const result = errorText({ code: 'SETTINGS_SAVE_FAILED', details: { diagnostics: {
    diagnostic: 'Permission denied', exception_stack: trace,
    recovery_diagnostic: 'Rollback failed: original.json is locked password=private-value',
    cause_type: 'PermissionError', exception_site: 'config:save:42', stage: 'apply',
    errno: 13, winerror: 5, plugin_id: 'sakura.provider', section_id: 'connection',
    has_application_state: false,
  } } });
  for (const value of [trace, 'PermissionError', 'config:save:42', 'apply', '13', '5',
    'Rollback failed', 'sakura.provider', 'connection', 'false']) assert.ok(result.includes(value), value.slice(0, 100));
  assert.ok(!result.includes('private-value'));
});

test('AggregateError retains independent underlying failures', () => {
  const result = errorText(new AggregateError([new Error('first connection refused'), new Error('second timed out')], 'providers failed'));
  assert.ok(result.includes('first connection refused'));
  assert.ok(result.includes('second timed out'));
});
