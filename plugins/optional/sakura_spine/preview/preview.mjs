import { createRenderer } from '/renderer.mjs';
import { createEditor, labelFor } from '/editor.mjs';

const byId = id => document.getElementById(id);
let renderer, editor, mountAbort, modelId, bindingId, epoch = 0, deliveryEpoch = 0, sequence = 0, paused = false;
let pending = Promise.resolve();
let draftRevision = 0;
const drafts = new Map();
const messages = {
  SPINE_WEBGL_UNAVAILABLE: '无法创建 WebGL 预览，请开启浏览器硬件加速后重试',
  SPINE_WEBGL_CONTEXT_LOST: '图形上下文已丢失，请重新选择资源',
  SPINE_TEXTURE_TOO_LARGE: '贴图超过当前设备支持的尺寸',
  SPINE_VERSION_UNSUPPORTED: '此插件支持 Spine 3.6 JSON 资源',
};
function errorMessage(error) {
  byId('error').hidden = false;
  byId('error').textContent = messages[error.message] || `角色加载失败（${error.message}）`;
  byId('status').textContent = '加载失败';
}
async function post(path, body) {
  const response = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error);
  return result;
}
function preview(payload) {
  const token = epoch, delivery = deliveryEpoch, target = modelId;
  const task = pending.catch(() => {}).then(async () => {
    if (token !== epoch || delivery !== deliveryEpoch) return;
    const parsed = await post('/api/preview', { modelId: target, payload });
    if (token === epoch && delivery === deliveryEpoch && renderer) renderer.applyControl({ version: 1, resourceId: target, bindingId, ...parsed }, { sequence: ++sequence });
  });
  pending = task;
  return task;
}
async function load(id) {
  const token = ++epoch;
  modelId = id;
  mountAbort?.abort(); renderer?.dispose(); editor?.dispose();
  renderer = undefined; editor = undefined; sequence = 0; pending = Promise.resolve();
  byId('error').hidden = true; byId('status').textContent = '正在加载';
  byId('save').disabled = true; byId('pause').disabled = true; byId('cancel').disabled = true;
  byId('saved').textContent = ''; byId('playing').textContent = '';
  mountAbort = new AbortController();
  bindingId = crypto.randomUUID();
  try {
    const response = await fetch(`/api/describe/${encodeURIComponent(id)}`, { signal: mountAbort.signal });
    if (!response.ok) throw new Error('SPINE_RESOURCE_INVALID');
    const description = await response.json();
    if (token !== epoch) return;
    const draft = drafts.get(id);
    if (draft) description.rendererData.config = structuredClone(draft.config);
    const local = await createRenderer({ container: byId('stage'), rendererData: description.rendererData,
      bindingId, resourceId: id, signal: mountAbort.signal,
      resolveAssetUrl: relative => `/assets/${encodeURIComponent(id)}/${relative.split('/').map(encodeURIComponent).join('/')}`,
      onError: error => { if (token === epoch) { errorMessage(error); byId('pause').disabled = true; byId('cancel').disabled = true; } },
    });
    if (token !== epoch) { local.dispose(); return; }
    renderer = local;
    editor = createEditor({ container: byId('editor'), rendererData: description.rendererData,
      onChange: config => {
        drafts.set(id, { config, revision: ++draftRevision });
        byId('saved').textContent = '未保存'; byId('save').disabled = false;
      }, onPreview: preview, onRenderingChange: () => load(id) });
    byId('saved').textContent = draft ? '未保存' : '';
    byId('save').disabled = !draft;
    byId('version').textContent = `Spine ${description.rendererData.runtimeVersion}`;
    byId('protocol').textContent = description.prompt + '\n\n' + JSON.stringify(description.outputSchema, null, 2);
    byId('status').textContent = '预览就绪';
    byId('pause').disabled = false; byId('cancel').disabled = false;
    paused = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    renderer.setPaused(paused);
    byId('pause').textContent = paused ? '播放' : '暂停';
  } catch (error) { if (token === epoch && error.name !== 'AbortError') errorMessage(error); }
}
byId('models').addEventListener('change', event => { void load(event.target.value); });
byId('background').addEventListener('change', event => { document.querySelector('.stage-area').dataset.background = event.target.value; });
byId('pause').addEventListener('click', () => { paused = !paused; renderer?.setPaused(paused); byId('pause').textContent = paused ? '播放' : '暂停'; });
byId('cancel').addEventListener('click', () => { ++deliveryEpoch; renderer?.cancel(); pending = Promise.resolve(); });
byId('save').addEventListener('click', async () => {
  const token = epoch, target = modelId, revision = drafts.get(modelId)?.revision;
  byId('save').disabled = true;
  try {
    await post('/api/draft', { modelId: target, config: editor.getDraft() });
    const unchanged = drafts.get(target)?.revision === revision;
    if (unchanged) drafts.delete(target);
    if (token === epoch && unchanged) { byId('saved').textContent = '草稿已保存'; byId('save').disabled = true; }
  } catch { if (token === epoch) { byId('saved').textContent = '草稿保存失败'; byId('save').disabled = false; } }
});
const timer = setInterval(() => {
  const state = renderer?.snapshot();
  if (state && !state.disposed) byId('playing').textContent = labelFor(state.playing, 'animation') + (paused ? ' · 已暂停' : '');
}, 200);
window.addEventListener('pagehide', () => { ++epoch; clearInterval(timer); mountAbort?.abort(); renderer?.dispose(); editor?.dispose(); }, { once: true });
try {
  const response = await fetch('/api/catalog');
  if (!response.ok) throw new Error('SPINE_RESOURCE_INVALID');
  const catalog = await response.json();
  for (const item of catalog.models) {
    const option = document.createElement('option'); option.value = item.resource.id; option.textContent = item.name; byId('models').append(option);
  }
  byId('models').value = catalog.default; byId('models').disabled = false;
  await load(catalog.default);
} catch (error) { errorMessage(error); }
