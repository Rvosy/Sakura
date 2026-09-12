const skinLabels = { default: '基础皮肤', normal: '平静', anger: '生气', sad: '难过', shy: '害羞', smile: '微笑', surprise: '惊讶' };
const animationLabels = { idle: '待机', attack: '攻击', damage: '受伤', death: '倒下', dying: '虚弱', skill: '技能', home: '展示', animation: '动画' };
export function labelFor(name, type, labels = {}) {
  if (type === 'skin' && Object.hasOwn(labels, name) && labels[name].trim()) return labels[name].trim();
  const defaults = type === 'skin' ? skinLabels : animationLabels;
  return Object.hasOwn(defaults, name) ? defaults[name] : name;
}

export function createEditor({ container, rendererData, onChange = () => {}, onPreview = () => {}, onRenderingChange = () => {} }) {
  let draft = structuredClone(rendererData.config);
  const doc = container.ownerDocument;
  const element = doc.createElement('div');
  element.className = 'spine-editor';
  const root = element;
  const style = new doc.defaultView.CSSStyleSheet();
  style.replaceSync(`
    .spine-editor { display:block; min-width:0; color:inherit; font:inherit; }
    .spine-editor h3 { font-size:14px; margin:25px 0 12px; font-weight:600; }
    .spine-editor h3:first-child { margin-top:0; }
    .spine-editor .spine-choices { display:flex; flex-wrap:wrap; gap:8px; }
    .spine-editor .spine-choices button { min-width:0; max-width:100%; overflow-wrap:anywhere; }
    .spine-editor .spine-field { display:grid; gap:9px; font-size:13px; margin:20px 0; }
    .spine-editor .spine-field select, .spine-editor .spine-field input[type="text"] { max-width:none; min-width:0; width:100%; }
    .spine-editor .layout-slider { width:100%; }
    .spine-editor .spine-error { color:var(--sakura-accent); font-size:13px; }
  `);
  doc.adoptedStyleSheets = [...doc.adoptedStyleSheets, style];
  const events = new AbortController();
  let disposed = false;
  const buttons = [];
  const error = doc.createElement('p');
  error.className = 'spine-error';
  error.setAttribute('role', 'alert');
  async function preview(payload) {
    error.textContent = '';
    try { await onPreview(payload); }
    catch { if (!disposed) error.textContent = '预览失败，请重新加载'; }
  }
  function changed() { onChange(structuredClone(draft)); }
  function heading(text) { const h = doc.createElement('h3'); h.textContent = text; root.append(h); }
  heading('表情');
  const skins = doc.createElement('div');
  skins.className = 'spine-choices';
  for (const name of rendererData.skins) {
    const button = doc.createElement('button');
    button.type = 'button';
    button.className = name === draft.defaultSkin ? 'primary-button compact-button' : 'secondary-button compact-button';
    button.value = name;
    button.textContent = labelFor(name, 'skin', draft.skinLabels);
    button.setAttribute('aria-pressed', String(name === draft.defaultSkin));
    button.addEventListener('click', () => {
      draft.defaultSkin = name;
      for (const [value, node] of buttons) {
        node.setAttribute('aria-pressed', String(name === value));
        node.className = name === value ? 'primary-button compact-button' : 'secondary-button compact-button';
      }
      skinName.value = draft.skinLabels?.[name] ?? labelFor(name, 'skin');
      changed();
      void preview({ skin: name });
    }, { signal: events.signal });
    buttons.push([name, button]);
    skins.append(button);
  }
  root.append(skins);
  const nameLabel = doc.createElement('label');
  nameLabel.className = 'spine-field'; nameLabel.textContent = '表情名称';
  const skinName = doc.createElement('input');
  skinName.type = 'text'; skinName.maxLength = 120; skinName.setAttribute('aria-label', '表情名称');
  skinName.value = draft.skinLabels?.[draft.defaultSkin] ?? labelFor(draft.defaultSkin, 'skin');
  skinName.addEventListener('input', () => {
    draft.skinLabels = { ...draft.skinLabels, [draft.defaultSkin]: skinName.value };
    for (const [name, button] of buttons) button.textContent = labelFor(name, 'skin', draft.skinLabels);
    changed();
  }, { signal: events.signal });
  nameLabel.append(skinName); root.append(nameLabel);
  const loopLabel = doc.createElement('label');
  loopLabel.className = 'spine-field'; loopLabel.textContent = '循环动画';
  const select = doc.createElement('select');
  for (const name of rendererData.animations) {
    const option = doc.createElement('option'); option.value = name; option.textContent = labelFor(name, 'animation'); select.append(option);
  }
  select.value = draft.defaultAnimation;
  select.addEventListener('change', () => { draft.defaultAnimation = select.value; changed(); void preview({ animation: select.value }); }, { signal: events.signal });
  loopLabel.append(select);
  if (rendererData.animations.length > 1) root.append(loopLabel);
  const speedLabel = doc.createElement('label');
  speedLabel.className = 'spine-field';
  const speedText = doc.createElement('span');
  speedText.textContent = `播放速度 · ${draft.speed.toFixed(1)}×`;
  const speed = doc.createElement('input');
  Object.assign(speed, { type: 'range', min: '0.1', max: '3', step: '0.1', value: String(draft.speed) });
  speed.className = 'layout-slider';
  speed.setAttribute('aria-label', '播放速度');
  speed.addEventListener('input', () => { draft.speed = Number(speed.value); speedText.textContent = `播放速度 · ${draft.speed.toFixed(1)}×`; changed(); void preview({ speed: draft.speed }); }, { signal: events.signal });
  speedLabel.append(speedText, speed); root.append(speedLabel);
  if (rendererData.animations.length > 1) heading('动作');
  const actions = doc.createElement('div'); actions.className = 'spine-choices';
  for (const name of rendererData.animations) {
    const button = doc.createElement('button'); button.type = 'button'; button.className = 'secondary-button compact-button'; button.textContent = labelFor(name, 'animation');
    button.addEventListener('click', () => { void preview({ action: name }); }, { signal: events.signal });
    actions.append(button);
  }
  if (rendererData.animations.length > 1) root.append(actions);
  const alphaLabel = doc.createElement('label');
  alphaLabel.className = 'spine-field';
  alphaLabel.textContent = '贴图透明方式';
  const alpha = doc.createElement('select');
  for (const [value, text] of [['false', '普通透明'], ['true', '预乘透明（PMA）']]) {
    const option = doc.createElement('option'); option.value = value; option.textContent = text; alpha.append(option);
  }
  alpha.value = String(draft.premultipliedAlpha);
  alpha.setAttribute('aria-label', '贴图透明方式');
  alpha.addEventListener('change', () => {
    draft.premultipliedAlpha = alpha.value === 'true'; changed();
    Promise.resolve(onRenderingChange(structuredClone(draft))).catch(() => {
      if (!disposed) error.textContent = '预览失败，请重新加载';
    });
  }, { signal: events.signal });
  alphaLabel.append(alpha); root.append(alphaLabel);
  root.append(error);
  container.append(element);
  return { getDraft: () => structuredClone(draft), freeze() { events.abort(); element.inert = true; },
    dispose() { disposed = true; events.abort(); element.remove(); doc.adoptedStyleSheets = doc.adoptedStyleSheets.filter(sheet => sheet !== style); } };
}

export { mountEditor } from './studio.mjs';
