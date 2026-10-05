import { safeErrorText } from './runtime-diagnostics.js';

// Keep source diagnostics as text; never render external error content as HTML.
export function errorText(error, fallback = '未提供错误详情') {
  const parts = [], seen = new Set();
  function add(value, label = '') {
    if (typeof value !== 'string' && typeof value !== 'number' && typeof value !== 'boolean') return;
    const text = String(value).trim();
    if (!text) return;
    const part = label ? `${label}：${text}` : text;
    if (!parts.includes(part)) parts.push(part);
  }
  function visit(value) {
    if (value == null || seen.has(value)) return;
    seen.add(value);
    if (typeof value === 'string') {
      // shell_lifecycle::settings_response_payload keeps these routing fields
      // separate from the diagnostic, which may itself contain pipes/newlines.
      const envelope = value.match(/^([A-Z][A-Z0-9_]{0,63})\|([^|\r\n]*)\|([^|\r\n]*)\|([\s\S]*)$/);
      if (envelope) {
        add(envelope[4]);
        add(envelope[1], '错误码');
        add(envelope[2], '功能');
        add(envelope[3], '字段');
      } else add(value);
      return;
    }
    visit(value.details?.diagnostics);
    visit(value.diagnostics);
    add(value.diagnostic);
    visit(value.message);
    add(value.exception_chain, '异常链');
    add(value.exception_stack, '调用栈');
    add(value.stack, '调用栈');
    add(value.recovery_diagnostic, '回滚报错');
    add(value.error_type || value.name, '类型');
    for (const [key, label] of [
      ['cause_type', '根因类型'], ['cause_code', '底层原因码'],
      ['exception_site', '代码位置'], ['stage', '阶段'], ['validation_field', '校验字段'],
      ['errno', '系统错误码'], ['winerror', 'Windows 错误码'],
      ['plugin_id', '插件 ID'], ['section_id', '设置分区'],
      ['result_type', '返回类型'], ['has_application_state', '包含应用状态'],
      ['application_state_type', '应用状态类型'],
    ]) add(value[key], label);
    add(value.code || value.errorCode, '错误码');
    add(value.reason_code, '原因码');
    if (value.relativePath) add(`${value.relativePath}${value.line ? `:${value.line}` : ''}`, '代码位置');
    visit(value.cause);
    visit(value.error);
    // Native AggregateError carries independent failures in errors.
    if (Array.isArray(value.errors)) value.errors.forEach(visit);
    add(value.diagnosticLog, '诊断日志');
  }
  visit(error);
  // Source boundaries already bound remote diagnostics. Do not cut off another
  // portion of the traceback while preparing it for display or copying.
  return safeErrorText(parts.join('\n\n') || fallback, Infinity);
}
