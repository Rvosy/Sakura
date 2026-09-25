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
