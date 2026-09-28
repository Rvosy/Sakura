import { safeErrorText } from './runtime-diagnostics.js';

// Keep protocol codes and source diagnostics together at every user boundary.
// Render the result as text, never HTML.
export function errorText(error, fallback = '未提供错误详情') {
  const parts = [], seen = new Set();
  function add(value) {
    if (typeof value === 'string' && value.trim() && !parts.includes(value.trim())) parts.push(value.trim());
  }
  function visit(value) {
    if (!value || seen.has(value)) return;
    seen.add(value);
    if (typeof value === 'string') { add(value); return; }
    add(value.code || value.errorCode);
    add(value.message);
    add(value.stack);
    add(value.diagnostic);
    add(value.exception_chain);
    add(value.exception_stack);
    if (value.relativePath) add(`${value.relativePath}${value.line ? `:${value.line}` : ''}`);
    visit(value.details?.diagnostics);
    visit(value.diagnostics);
    visit(value.cause);
    visit(value.error);
    if (value.diagnosticLog) add(value.diagnosticLog);
  }
  visit(error);
  return safeErrorText(parts.join('\n') || fallback, 16384);
}
