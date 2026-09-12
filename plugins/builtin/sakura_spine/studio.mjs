import { createEditor } from './editor.mjs';
import { createRenderer } from './renderer.mjs';

function relative(value) {
  if (typeof value !== 'string' || !value || /[\\:%?#\x00-\x1f]/.test(value)
    || value.split('/').some(part => !part || part === '.' || part === '..')) throw new Error('SPINE_RESOURCE_PATH_INVALID');
  return value;
}

export async function readEditorResource(config, read) {
  const skeleton = await read(relative(config.skeleton), 'json');
  if (!/^3\.6\.\d+$/.test(skeleton.skeleton?.spine)) throw new Error('SPINE_VERSION_UNSUPPORTED');
  const allSkins = Object.keys(skeleton.skins || {}), animations = Object.keys(skeleton.animations || {});
  const skins = 'selectableSkins' in config ? config.selectableSkins : allSkins;
  if (!Array.isArray(skins) || !skins.length || new Set(skins).size !== skins.length
    || skins.some(name => !allSkins.includes(name))) throw new Error('SPINE_CONFIG_INVALID');
  if (!skins.length || !animations.length || !skeleton.bones?.length) throw new Error('SPINE_SKELETON_INCOMPLETE');
  const atlas = relative(config.atlas), prefix = atlas.slice(0, atlas.lastIndexOf('/') + 1);
  const text = await read(atlas, 'text');
  const textures = {};
  for (const block of text.replaceAll('\r\n', '\n').trim().split(/\n\s*\n/)) {
    const page = relative(block.split('\n')[0].trim());
    if (!/\.(png|jpe?g)$/i.test(page)) throw new Error('SPINE_ATLAS_INVALID');
    textures[page] = prefix + page;
  }
  const normalized = { version: 1, defaultSkin: skins.includes('default') ? 'default' : skins[0],
    defaultAnimation: animations.includes('idle') ? 'idle' : animations[0], speed: 1, premultipliedAlpha: false,
    modelControls: animations.length === 1 ? ['skin'] : ['skin', 'animation', 'speed', 'action'], ...config };
  const labels = config.skinLabels ?? {};
  if (('skinLabels' in config && config.skinLabels === null) || typeof labels !== 'object' || Array.isArray(labels)
    || Object.entries(labels).some(([name, text]) => !allSkins.includes(name) || typeof text !== 'string' || [...text].length > 120)) throw new Error('SPINE_CONFIG_INVALID');
  if (!skins.includes(normalized.defaultSkin)) throw new Error('SPINE_DEFAULT_INVALID');
  return { runtimeVersion: skeleton.skeleton.spine, config: normalized, skins, animations, textures };
}

export function mountEditor({ container, data, host, signal }) {
  const doc = container.ownerDocument;
  const element = doc.createElement('div');
  element.className = 'spine-studio';
  const root = element;
  const style = new doc.defaultView.CSSStyleSheet();
  style.replaceSync(`
    .spine-studio { display:block; min-width:0; }
    .spine-studio-layout { display:grid; grid-template-columns:minmax(0,1fr) minmax(180px,1fr); gap:20px; align-items:start; }
    .spine-studio-layout:empty, .spine-studio-error:empty { display:none; }
    .spine-studio-preview { height:420px; min-width:0; position:relative; overflow:hidden; border:1px solid var(--sakura-border); border-radius:12px; background:var(--sakura-panel-bg); cursor:grab; touch-action:none; }
    .spine-studio-preview.is-dragging { cursor:grabbing; }
    .spine-studio-error { color:var(--sakura-accent); }
    .spine-preview-pane { min-width:0; position:sticky; top:0; }
    .spine-preview-controls { display:flex; align-items:center; gap:12px; margin-top:12px; }
    .spine-preview-controls label { flex:1; display:flex; align-items:center; gap:10px; min-width:0; }
    .spine-preview-controls input { flex:1; min-width:40px; }
    .spine-preview-controls output { min-width:4ch; text-align:right; }
    .spine-studio-import { display:flex; justify-content:flex-end; margin-top:16px; }
    .spine-studio > .spine-studio-import { justify-content:flex-start; margin-top:0; }
    @media(max-width:800px) { .spine-studio-layout { grid-template-columns:1fr; } .spine-studio-preview { height:320px; } .spine-preview-pane { position:static; } }
  `);
  doc.adoptedStyleSheets = [...doc.adoptedStyleSheets, style];
  const button = doc.createElement('button');
  button.type = 'button'; button.className = 'secondary-button'; button.textContent = '导入模型目录';
  const error = doc.createElement('p'); error.className = 'spine-studio-error'; error.setAttribute('role', 'alert');
  const layout = doc.createElement('div'); layout.className = 'spine-studio-layout';
  const importActions = doc.createElement('div'); importActions.className = 'spine-studio-import'; importActions.append(button);
  root.append(error, layout, importActions); container.append(element);
  let draft = structuredClone(data || {}), editor, renderer, loading, valid = false, revision = 0, disposed = false;
  const events = new AbortController();
  const view = { zoom: 1, x: 0, y: 0 };
  let refreshView = () => {};
  function freeze() { events.abort(); loading?.abort(); editor?.freeze(); element.inert = true; }
  signal.addEventListener('abort', freeze, { once: true });
  async function read(path, method, activeSignal = signal) {
    const url = await host.assetUrl(relative(path));
    if (activeSignal.aborted) throw new DOMException('Cancelled', 'AbortError');
    const response = await fetch(url, { signal: activeSignal });
    if (!response.ok) throw new Error('SPINE_ASSET_LOAD_FAILED');
    return response[method]();
  }
  async function load(config, changed = false) {
    const current = ++revision;
    valid = false;
    loading?.abort();
    loading = new AbortController();
    const active = loading.signal;
    let candidate, controls;
    const stage = doc.createElement('div'); stage.className = 'spine-studio-layout';
    const preview = doc.createElement('div'); preview.className = 'spine-studio-preview';
    const pane = doc.createElement('div'); pane.className = 'spine-preview-pane';
    const bar = doc.createElement('div'); bar.className = 'spine-preview-controls';
    const label = doc.createElement('label'); label.textContent = '缩放';
    const zoom = doc.createElement('input'); zoom.type = 'range'; zoom.className = 'layout-slider'; zoom.min = '50'; zoom.max = '400'; zoom.step = '1'; zoom.setAttribute('aria-label', '预览缩放');
    const value = doc.createElement('output');
    const reset = doc.createElement('button'); reset.type = 'button'; reset.className = 'secondary-button'; reset.textContent = '重置'; reset.setAttribute('aria-label', '重置预览');
    label.append(zoom, value); bar.append(label, reset); pane.append(preview, bar);
    const panel = doc.createElement('div'); stage.append(pane, panel);
    const applyView = () => {
      zoom.value = String(Math.round(view.zoom * 100)); value.textContent = `${zoom.value}%`;
      const canvas = preview.querySelector('canvas');
      if (canvas) canvas.style.transform = `translate(${view.x}px, ${view.y}px) scale(${view.zoom})`;
      candidate?.resize();
    };
    const setZoom = (percent, x = 0, y = 0) => {
      const next = Math.max(50, Math.min(400, Math.round(percent))) / 100;
      const ratio = next / view.zoom;
      view.x = x - (x - view.x) * ratio; view.y = y - (y - view.y) * ratio; view.zoom = next;
      applyView();
    };
    zoom.addEventListener('input', () => setZoom(Number(zoom.value)), { signal: active });
    reset.addEventListener('click', () => { Object.assign(view, { zoom: 1, x: 0, y: 0 }); applyView(); }, { signal: active });
    preview.addEventListener('wheel', event => {
      event.preventDefault();
      const rect = preview.getBoundingClientRect();
      const delta = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? rect.height : 1);
      setZoom(view.zoom * 100 * Math.exp(-delta / 500), event.clientX - rect.left - rect.width / 2, event.clientY - rect.top - rect.height / 2);
    }, { signal: active, passive: false });
    let drag;
    preview.addEventListener('pointerdown', event => {
      if (event.button !== 0 || !event.isPrimary) return;
      event.preventDefault();
      drag = { id: event.pointerId, x: event.clientX, y: event.clientY };
      preview.setPointerCapture(event.pointerId); preview.classList.add('is-dragging');
    }, { signal: active });
    preview.addEventListener('pointermove', event => {
      if (!drag || drag.id !== event.pointerId) return;
      view.x += event.clientX - drag.x; view.y += event.clientY - drag.y;
      drag.x = event.clientX; drag.y = event.clientY; applyView();
    }, { signal: active });
    const stopDrag = () => { if (drag && preview.hasPointerCapture(drag.id)) preview.releasePointerCapture(drag.id); drag = null; preview.classList.remove('is-dragging'); };
    for (const event of ['pointerup', 'pointercancel', 'lostpointercapture']) preview.addEventListener(event, stopDrag, { signal: active });
    active.addEventListener('abort', stopDrag, { once: true });
    try {
      const rendererData = await readEditorResource(config, (path, method) => read(path, method, active));
      if (signal.aborted || active.aborted) return;
      layout.append(stage);
      candidate = await createRenderer({ container: preview, rendererData, bindingId: 'editor', resourceId: 'editor',
        resolveAssetUrl: path => host.assetUrl(relative(path)), signal: active,
        onError: failure => { if (!signal.aborted && current === revision) { valid = false; error.textContent = '模型预览失败，请重新导入'; host.error(failure); } } });
      if (signal.aborted || active.aborted) { candidate.dispose(); stage.remove(); return; }
      let sequence = 0;
      controls = createEditor({ container: panel, rendererData, onChange(value) {
        if (signal.aborted || current !== revision) return;
        draft = value; host.changed(draft);
      }, onRenderingChange: value => load(value, true), onPreview(payload) {
        if (signal.aborted || current !== revision) return;
        const { action, ...state } = payload;
        if (!candidate.applyControl({ version: 1, bindingId: 'editor', resourceId: 'editor', state,
          actions: action ? [{ animation: action }] : [] }, { sequence: sequence++ })) throw new Error('SPINE_CONTROL_INVALID');
      } });
      editor?.dispose(); renderer?.dispose();
      editor = controls; renderer = candidate; layout.replaceChildren(stage); stage.className = '';
      stage.style.display = 'contents';
      panel.append(importActions);
      draft = rendererData.config; valid = true; error.textContent = '';
      refreshView = applyView;
      applyView();
      if (changed) host.changed(draft);
    } catch (failure) {
      candidate?.dispose(); controls?.dispose(); stage.remove();
      if (!signal.aborted && !active.aborted && current === revision) {
        error.textContent = '模型无法加载，请检查骨骼、图集和贴图'; host.error(failure);
      }
    }
  }
  button.addEventListener('click', async () => {
    button.disabled = true;
    try {
      const files = await host.importFiles({ folder: true });
      if (signal.aborted || !files.length) return;
      const entry = files.find(file => file.name === 'spine-resource.json');
      let config;
      if (entry) {
        config = await read(entry.resourcePath, 'json');
        const prefix = entry.resourcePath.slice(0, entry.resourcePath.lastIndexOf('/') + 1);
        config = { ...config, skeleton: prefix + relative(config.skeleton), atlas: prefix + relative(config.atlas) };
      } else {
        const skeletons = [];
        for (const file of files.filter(file => file.resourcePath.endsWith('.json'))) {
          const value = await read(file.resourcePath, 'json');
          if (value.bones?.length && Object.keys(value.animations || {}).length) skeletons.push({ file, value });
        }
        if (skeletons.length !== 1) throw new Error('请选择仅包含一套骨骼的模型目录');
        const { file, value } = skeletons[0];
        const stem = file.resourcePath.slice(0, -5);
        const atlas = files.find(item => [stem + '.atlas', stem + '.atlas.txt'].includes(item.resourcePath));
        if (!atlas) throw new Error('没有找到对应图集');
        config = { version: 1, skeleton: file.resourcePath, atlas: atlas.resourcePath };
        if (value.skins?.normal) config.defaultSkin = 'normal';
      }
      if (!signal.aborted) await load(config, true);
    } catch (failure) { if (!signal.aborted) { error.textContent = failure.message; host.error(failure); } }
    finally { if (!signal.aborted) button.disabled = false; }
  }, { signal: events.signal });
  const ready = draft.skeleton && draft.atlas ? load(draft) : Promise.resolve();
  if (signal.aborted) freeze();
  return { ready, collect: () => structuredClone(draft), validate: () => valid,
    snapshotView: () => ({ ...view }),
    restoreView(value) {
      if (signal.aborted || !value || ![value.zoom, value.x, value.y].every(Number.isFinite)
        || value.zoom < 0.5 || value.zoom > 4) return;
      Object.assign(view, { zoom: value.zoom, x: value.x, y: value.y }); refreshView();
    },
    destroy() {
      if (disposed) return;
      disposed = true; freeze(); signal.removeEventListener('abort', freeze);
      editor?.dispose(); renderer?.dispose(); element.remove();
      doc.adoptedStyleSheets = doc.adoptedStyleSheets.filter(sheet => sheet !== style);
    } };
}

export async function renderThumbnail({ container, data, host, signal }) {
  const rendererData = await readEditorResource(data, async (path, method) => {
    const response = await fetch(await host.assetUrl(path), { signal });
    if (!response.ok) throw new Error('SPINE_ASSET_LOAD_FAILED');
    return response[method]();
  });
  let renderer;
  try {
    renderer = await createRenderer({ container, rendererData, resolveAssetUrl: host.assetUrl,
      bindingId: 'thumbnail', resourceId: 'thumbnail', signal });
    renderer.setPaused(true);
    return renderer.capture();
  } finally { renderer?.dispose(); }
}
