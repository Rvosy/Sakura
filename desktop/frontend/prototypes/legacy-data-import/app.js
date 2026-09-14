import { openLegacyDataImport } from '../../settings/legacy-data-import.js';
const $ = (id) => document.getElementById(id);
let active = null, latest = null;
const delay = (ms) => new Promise(resolve => setTimeout(resolve, ms));
function makePlan(mapping = {}) {
  const scene = $('scenario').value;
  const rows = scene === 'empty' ? [] : [
    { id: 'Sakura', history: [1280,86,0], memory: [42,8,0] },
    { id: 'N.A.V.I.', history: [368,24,0], memory: [18,3,0] },
    { id: 'Haru', history: [92,0,0], memory: [6,0,0] },
  ];
  if (scene === 'conflict') { rows[0].history[2] = 3; rows[0].memory[2] = 2; }
  if (scene === 'duplicate') rows.forEach(row => ['history','memory'].forEach(kind => { row[kind][1] += row[kind][0]; row[kind][0] = 0; }));
  const totals = { historyNew:0, historyIdentical:0, historyConflicts:0, memoryNew:0, memoryIdentical:0, memoryConflicts:0, recoverableErrors:scene === 'dirty' ? 3 : 0 };
  const characters = rows.map(row => {
    const result = { characterId: mapping[row.id] || row.id };
    for (const kind of ['history','memory']) {
      result[kind] = {};
      ['new','identical','conflicts'].forEach((key,index) => { result[kind][key] = row[kind][index]; totals[kind+key[0].toUpperCase()+key.slice(1)] += row[kind][index]; });
    }
    return result;
  });
  const packages = rows.map(row => {
    const target = mapping[row.id] || row.id;
    const isExisting = scene === 'duplicate' || ['Haru','NaviCurrent'].includes(target);
    return { sourceCharacterId:row.id, targetCharacterId:target, displayName:row.id, packageStatus:isExisting ? 'existing' : 'new', canImport:true, requiresMapping:scene !== 'duplicate' && row.id === 'N.A.V.I.' && !mapping[row.id] };
  });
  return { schemaVersion:1, planToken:crypto.randomUUID().replaceAll('-',''), selectionId:'demo-selection', sourceLabel:'Sakura-0.9.9', characters, totals, packages, packagesNew:packages.filter(p=>p.packageStatus==='new').length, targetCharacters:[{characterId:'NaviCurrent',displayName:'N.A.V.I.'},{characterId:'Haru',displayName:'Haru'}], packageIssues:[], conflicts:[], charactersTruncated:false, blocked:false, requiresMapping:packages.some(p=>p.requiresMapping), requiresConflictConfirmation:scene==='conflict', reassociatedRecords:0 };
}
const client = {
  async legacyRoleDataImportChoose(selectionId, mapping) {
    await delay(400);
    if ($('scenario').value === 'invalid') throw Error('LEGACY_DATA_SOURCE_UNRECOGNIZED');
    latest = makePlan(mapping); return latest;
  },
  async legacyRoleDataImportApply(selectionId, planToken, overwrite) {
    if (!latest || latest.planToken !== planToken || latest.requiresMapping || (latest.requiresConflictConfirmation && !overwrite)) throw Error('LEGACY_DATA_IMPORT_CONFIRMATION_REQUIRED');
    await delay(600);
    return { schemaVersion:1, importId:'demo-result', outcome:'completed', plan:latest };
  },
};
$('open').onclick = () => { if (active?.dialog.isConnected) return; active = openLegacyDataImport({client,setBusy:busy=>{$('scenario').disabled=busy;},onComplete:()=>{$('page-status').textContent='演示迁移完成。';}}); };
$('scenario').onchange = () => { active?.close(); latest = null; };
$('open-data').onclick = () => { $('page-status').textContent='演示不打开真实数据目录。'; };
