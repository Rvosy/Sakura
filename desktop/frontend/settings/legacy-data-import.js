import { enhanceSelect, refreshSelect, closeSelects } from './select-control.js';
import { legacyDataImportPlanHasWork } from './root-settings-runtime.js';

export function legacyImportError(error) {
  const code = String(error);
  const messages = {
    LEGACY_SOURCE_ACTIVE: '请先退出 Sakura 0.9.x，再选择目录。',
    LEGACY_DATA_SOURCE_UNRECOGNIZED: '所选目录不是可识别的 Sakura 0.9.x 数据目录。',
    LEGACY_DATA_MAPPING_REQUIRED: '请先选择待关联角色的迁移目标。',
    LEGACY_DATA_MAPPING_COLLISION: '多个旧角色不能迁移到同一个角色，请分别选择。',
    LEGACY_DATA_MAPPING_INVALID: '所选角色已不可用，请重新扫描。',
    LEGACY_DATA_SCOPE_CONFLICT: '部分记录的角色身份冲突，不能覆盖其他角色的数据。',
    LEGACY_DATA_IMPORT_CONFIRMATION_REQUIRED: '当前有冲突记录，请重新扫描后确认覆盖。',
    LEGACY_DATA_IMPORT_PLAN_STALE: '迁移选择已失效，请重新选择目录。',
    LEGACY_IMPORT_PROCESS_TERMINATION_FAILED: '无法确认迁移进程已停止。请退出 Sakura，保留迁移记录并重启系统后重试。',
    LEGACY_IMPORT_CORE_STOP_FAILED: '无法确认 Sakura Core 已停止。请退出 Sakura，保留迁移记录并重启系统后重试。',
    LEGACY_IMPORT_OPERATION_TIMEOUT: '迁移等待超时，请重新扫描后重试。',
  };
  return Object.entries(messages).find(([key]) => code.includes(key))?.[1] || `迁移失败：${code}`;
}

export function openLegacyDataImport({ client, setBusy = () => {}, onComplete = () => {} }) {
  const dialog = document.createElement('dialog');
  dialog.className = 'legacy-import-dialog';
  dialog.setAttribute('aria-label', '迁移角色和数据');
  dialog.innerHTML = `
    <header class="legacy-import-head"><div><p>Sakura 0.9.x</p><h2>迁移角色和数据</h2></div><button type="button" class="legacy-import-close" aria-label="关闭弹窗">×</button></header>
    <div class="legacy-import-body">
      <section class="legacy-import-source"><img src="../assets/sakura-icon.png" alt="" width="36" height="36"><div><strong>旧版数据目录</strong><p data-source>尚未选择</p></div><button type="button" data-choose class="secondary-button">选择目录</button></section>
      <p data-feedback role="status" aria-live="polite"></p>
      <section data-results hidden></section>
      <div data-empty class="legacy-import-empty">选择目录后查看角色包、聊天记录和记忆</div>
      <section data-completed class="legacy-import-completed" hidden><span aria-hidden="true">✓</span><h3>迁移完成</h3><p data-receipt></p></section>
    </div>
    <footer class="legacy-import-footer"><span data-summary></span><div><button type="button" data-cancel class="secondary-button">取消</button><button type="button" data-start class="primary-button" disabled>开始迁移</button></div></footer>`;
  const $ = (selector) => dialog.querySelector(selector);
  dialog.querySelector("img").src = new URL("../assets/sakura-icon.png", import.meta.url).href;
  const source = $('[data-source]'), feedback = $('[data-feedback]'), results = $('[data-results]');
  const choose = $('[data-choose]'), start = $('[data-start]'), cancel = $('[data-cancel]');
  const closeButton = $('.legacy-import-close');
  let plan = null, busy = false, complete = false, alive = true, mapping = {};
  let confirmation = null;
  const count = (value) => Number(value || 0).toLocaleString('zh-CN');
  const sync = () => {
    for (const button of [choose, cancel, closeButton]) button.disabled = busy;
    choose.disabled = busy || complete;
    for (const select of results.querySelectorAll('select')) { select.disabled = busy; refreshSelect(select); }
    if (confirmation) confirmation.disabled = busy;
    start.disabled = busy || (!complete && (!plan || plan.blocked || plan.requiresMapping || !legacyDataImportPlanHasWork(plan) || (plan.requiresConflictConfirmation && !confirmation?.checked)));
    dialog.setAttribute('aria-busy', String(busy));
  };
  const setOperation = (value) => { busy = value; setBusy(value); sync(); };
  const close = () => { if (busy) return; alive = false; closeSelects(dialog); dialog.close(); dialog.remove(); };
  closeButton.onclick = close; cancel.onclick = close;
  dialog.addEventListener('cancel', (event) => { event.preventDefault(); close(); });
  const text = (tag, content, className = '') => { const el = document.createElement(tag); el.textContent = content; el.className = className; return el; };
  const render = () => {
    const expanded = new Set([...results.querySelectorAll("details[open]")].map(node => node.dataset.role));
    closeSelects(dialog); results.replaceChildren(); confirmation = null;
    if (!plan) { results.hidden = true; sync(); return; }
    source.textContent = plan.sourceLabel; choose.textContent = '更换目录';
    results.hidden = false; $('[data-empty]').hidden = true;
    const totals = plan.totals;
    const packages = plan.packages || [];
    const headings = text('div', '', 'legacy-import-result-head');
    headings.append(text('h3', '扫描结果'), text('span', `${packages.length || plan.characters.length} 个角色`)); results.append(headings);
    const stats = text('div', '', 'legacy-import-totals');
    for (const [label, value] of [['角色包', new Set(packages.filter(p => ['new', 'existing'].includes(p.packageStatus)).map(p => p.targetCharacterId)).size], ['聊天记录', totals.historyNew + totals.historyIdentical + totals.historyConflicts], ['记忆', totals.memoryNew + totals.memoryIdentical + totals.memoryConflicts]]) {
      const item = text('div', ''); item.append(text('span', label), text('strong', count(value))); stats.append(item);
    }
    results.append(stats);
    const header = text('div', '', 'legacy-import-row legacy-import-columns');
    for (const label of ['角色 / 数据类型', '新增', '重复', '冲突']) header.append(text('span', label)); results.append(header);
    const entries = packages.length ? packages : plan.characters.map(c => ({ sourceCharacterId: c.characterId, targetCharacterId: c.characterId, displayName: c.characterId, packageStatus: 'missing' }));
    for (const [index, entry] of entries.entries()) {
      const counts = plan.characters.find(c => c.characterId === entry.targetCharacterId) || { history: {}, memory: {} };
      const countOwner = entries.find(e => e.sourceCharacterId === entry.targetCharacterId && e.targetCharacterId === entry.targetCharacterId) || entries.find(e => e.targetCharacterId === entry.targetCharacterId);
      const sharesCounts = countOwner !== entry;
      const group = document.createElement('details'); group.dataset.role = entry.sourceCharacterId; group.open = expanded.has(entry.sourceCharacterId) || index === 0 || entry.requiresMapping;
      const summary = text('summary', '', 'legacy-import-row');
      const name = text('span', '', 'legacy-import-role'); name.append(text('span', entry.displayName.slice(0,1), 'legacy-import-avatar'), text('strong', entry.displayName)); summary.append(name);
      for (const key of ['new', 'identical', 'conflicts']) summary.append(text('span', sharesCounts ? '—' : count((counts.history[key] || 0) + (counts.memory[key] || 0)))); group.append(summary);
      if (sharesCounts) group.append(text('p', `数据计入 ${countOwner.displayName}。`, 'legacy-import-package'));
      for (const [key, label] of sharesCounts ? [] : [['history', '聊天记录'], ['memory', '记忆']]) {
        const line = text('div', '', 'legacy-import-row legacy-import-data'); line.append(text('span', label));
        for (const kind of ['new', 'identical', 'conflicts']) line.append(text('span', count(counts[key][kind]))); group.append(line);
      }
      const states = { new: '随数据导入', existing: '保留当前角色包', missing: '未找到角色包，保留数据', unavailable: '角色包无法加载，保留数据' };
      const packageLine = text('div', '', 'legacy-import-package'); packageLine.append(text('span', '角色包'), text('span', entry.sourceCharacterId !== entry.targetCharacterId ? '使用所选角色包' : states[entry.packageStatus] || '未找到角色包')); group.append(packageLine);
      if ((plan.targetCharacters || []).length || entry.requiresMapping) {
        const label = text('label', '', 'legacy-import-mapping'); label.append(text('span', '迁移到'));
        const select = document.createElement('select'); select.setAttribute('aria-label', `${entry.displayName} 的迁移目标`);
        const option = (value, title) => { const node = text('option', title); node.value = value; select.append(node); };
        if (entry.requiresMapping) option('', '选择角色');
        if (!(plan.targetCharacters || []).some(c => c.characterId === entry.sourceCharacterId)) option(entry.sourceCharacterId, entry.canImport ? `导入旧版 ${entry.displayName}` : '保留原角色 ID');
        for (const target of plan.targetCharacters || []) option(target.characterId, `${target.displayName}（${target.characterId}）`);
        select.value = entry.requiresMapping ? '' : entry.targetCharacterId;
        select.onchange = () => { if (!select.value || !plan) return; mapping = { ...mapping, [entry.sourceCharacterId]: select.value }; void scan(plan.selectionId); };
        label.append(select); group.append(label);
      }
      results.append(group);
    }
    results.querySelectorAll('select').forEach(enhanceSelect);
    if (plan.reassociatedRecords) results.append(text('p', `将关联当前数据中 ${count(plan.reassociatedRecords)} 条未匹配角色的记录。`, 'legacy-import-warning'));
    if (totals.recoverableErrors) results.append(text('p', `${count(totals.recoverableErrors)} 条坏数据将单独保留，可用记录继续迁移。`, 'legacy-import-warning'));
    for (const issue of plan.packageIssues || []) results.append(text('p', `${issue.name}：角色包无法读取，继续迁移其他数据。`, 'legacy-import-warning'));
    if (plan.charactersTruncated) results.append(text('p', '仅展示前 256 个角色，迁移包含全部已扫描数据。', 'legacy-import-warning'));
    if (plan.requiresConflictConfirmation && !plan.blocked) {
      const label = text('label', '', 'legacy-import-warning'); confirmation = document.createElement('input'); confirmation.type = 'checkbox';
      confirmation.onchange = sync; label.append(confirmation, text('span', `同意覆盖 ${count(totals.historyConflicts + totals.memoryConflicts)} 条冲突记录，当前版本中的这些记录将被替换。`)); results.append(label);
    }
    if (plan.blocked) feedback.textContent = legacyImportError('LEGACY_DATA_SCOPE_CONFLICT');
    $('[data-summary]').textContent = plan.requiresMapping ? '请选择待关联角色' : legacyDataImportPlanHasWork(plan) ? `待导入 ${count(plan.packagesNew)} 个角色包 · 新增 ${count(totals.historyNew + totals.memoryNew)} 条` : '没有新数据';
    sync();
  };
  async function scan(selectionId = null) {
    if (busy) return;
    const previous = plan;
    const previousMapping = mapping;
    if (!selectionId) mapping = {};
    setOperation(true); feedback.textContent = selectionId ? '正在按所选角色重新扫描…' : '正在选择并扫描旧目录…';
    complete = false; $('[data-completed]').hidden = true; start.textContent = '开始迁移';
    try {
      const next = await client.legacyRoleDataImportChoose(selectionId, mapping);
      if (!alive) return;
      plan = next || previous;
      if (!next) mapping = previousMapping;
      feedback.textContent = ''; render();
    } catch (error) { plan = null; results.hidden = true; closeSelects(dialog); feedback.textContent = legacyImportError(error); }
    finally { setOperation(false); }
  }
  choose.onclick = () => void scan();
  start.onclick = async () => {
    if (complete) { close(); return; }
    if (busy || start.disabled) return;
    const selected = plan;
    setOperation(true); start.textContent = '正在迁移…'; feedback.textContent = '正在迁移角色包、聊天记录和记忆，请勿退出 Sakura。';
    try {
      const report = await client.legacyRoleDataImportApply(selected.selectionId, selected.planToken, Boolean(confirmation?.checked));
      complete = true; results.hidden = true; $('[data-completed]').hidden = false; feedback.textContent = '';
      const actual = report.plan?.totals || {};
      $('[data-receipt]').textContent = `导入 ${count(report.plan?.packagesNew)} 个角色包；新增 ${count(actual.historyNew + actual.memoryNew)} 条，跳过 ${count(actual.historyIdentical + actual.memoryIdentical)} 条，覆盖 ${count(actual.historyConflicts + actual.memoryConflicts)} 条，单独保留 ${count(actual.recoverableErrors)} 条坏数据，关联 ${count(report.plan?.reassociatedRecords)} 条已有记录。`;
      $('[data-summary]').textContent = ''; start.textContent = '完成'; cancel.hidden = true;
      try { await onComplete(report); } catch { feedback.textContent = "迁移已完成，角色列表刷新失败，请重新打开设置。"; }
    } catch (error) { plan = null; feedback.textContent = legacyImportError(error); start.textContent = '开始迁移'; }
    finally { setOperation(false); }
  };
  document.body.append(dialog); dialog.showModal();
  return { close, dialog };
}
