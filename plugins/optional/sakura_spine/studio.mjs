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
  const skins = Object.keys(skeleton.skins || {}), animations = Object.keys(skeleton.animations || {});
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
  return { runtimeVersion: skeleton.skeleton.spine, config: normalized, skins, animations, textures };
}

export function mountEditor({ container, data, host, signal }) {
  const doc = container.ownerDocument;
  const element = doc.createElement('div');
  const root = element.attachShadow({ mode: 'open' });
  const style = new CSSStyleSheet();
  style.replaceSync(`:host{display:block;--spine-accent:var(--sakura-primary,#755893);--spine-border:var(--sakura-border,#dfe1eb);--spine-surface:var(--sakura-card-bg,#fff)}
    button{font:inherit;font-size:13px;padding:9px 14px;cursor:pointer;border:1px solid var(--spine-border);border-radius:8px;background:var(--spine-surface);color:inherit}
    button:hover{border-color:var(--spine-accent)}button:focus-visible{outline:2px solid var(--spine-accent);outline-offset:2px}
    .layout{display:grid;grid-template-columns:minmax(0,1fr) minmax(180px,1fr);gap:20px;margin-top:16px}
    .preview{height:420px;min-width:0}.error{color:#b43f59} @media(max-width:800px){.layout{grid-template-columns:1fr}.preview{height:320px}}`);
  root.adoptedStyleSheets = [style];
  const button = doc.createElement('button');
  button.type = 'button'; button.textContent = '导入模型目录';
  const error = doc.createElement('p'); error.className = 'error'; error.setAttribute('role', 'alert');
  const layout = doc.createElement('div'); layout.className = 'layout';
  root.append(button, error, layout); container.append(element);
  let draft = structuredClone(data || {}), editor, renderer, loading, valid = false, revision = 0, disposed = false;
  const events = new AbortController();
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
    const stage = doc.createElement('div'); stage.className = 'layout';
    const preview = doc.createElement('div'); preview.className = 'preview';
    const panel = doc.createElement('div'); stage.append(preview, panel);
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
      }, onPreview(payload) {
        if (signal.aborted || current !== revision) return;
        const { action, ...state } = payload;
        if (!candidate.applyControl({ version: 1, bindingId: 'editor', resourceId: 'editor', state,
          actions: action ? [{ animation: action }] : [] }, { sequence: sequence++ })) throw new Error('SPINE_CONTROL_INVALID');
      } });
      editor?.dispose(); renderer?.dispose();
      editor = controls; renderer = candidate; layout.replaceChildren(stage); stage.className = '';
      stage.style.display = 'contents';
      draft = rendererData.config; valid = true; error.textContent = '';
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
    destroy() {
      if (disposed) return;
      disposed = true; freeze(); signal.removeEventListener('abort', freeze);
      editor?.dispose(); renderer?.dispose(); element.remove();
    } };
}
