const skinLabels = { default: '基础皮肤', normal: '平静', anger: '生气', sad: '难过', shy: '害羞', smile: '微笑', surprise: '惊讶' };
const animationLabels = { idle: '待机', attack: '攻击', damage: '受伤', death: '倒下', dying: '虚弱', skill: '技能', home: '展示', animation: '动画' };
export function labelFor(name, type) { return (type === 'skin' ? skinLabels : animationLabels)[name] || name; }

export function createEditor({ container, rendererData, onChange = () => {}, onPreview = () => {}, onRenderingChange = () => {} }) {
  let draft = structuredClone(rendererData.config);
  const element = document.createElement('div');
  const root = element.attachShadow({ mode: 'open' });
  const style = new CSSStyleSheet();
  style.replaceSync(`
    :host { display:block; color:inherit; font:inherit; }
    * { box-sizing:border-box; } h3 { font-size:14px; margin:25px 0 12px; font-weight:600; }
    h3:first-of-type { margin-top:0; } .choices { display:flex; flex-wrap:wrap; gap:8px; }
    button, select, input { font:inherit; } button { border:1px solid var(--spine-border,#dfe1eb); border-radius:8px;
      padding:8px 12px; background:var(--spine-surface,#fff); color:inherit; cursor:pointer; }
    button[aria-pressed=true] { background:var(--spine-accent,#755893); color:#fff; border-color:transparent; }
    button:hover { border-color:var(--spine-accent,#755893); } :focus-visible { outline:2px solid #9470b4; outline-offset:3px; }
    label { display:grid; gap:9px; font-size:13px; margin:20px 0; } select { border:1px solid var(--spine-border,#dfe1eb);
      border-radius:8px; padding:10px; background:var(--spine-surface,#fff); color:inherit; min-width:0; width:100%; }
    input[type=range] { accent-color:var(--spine-accent,#755893); width:100%; }
    .check { display:flex; align-items:center; gap:8px; } .error { color:#b43f59; font-size:13px; }
  `);
  root.adoptedStyleSheets = [style];
  const events = new AbortController();
  let disposed = false;
  const buttons = [];
  const error = document.createElement('p');
  error.className = 'error';
  error.setAttribute('role', 'alert');
  async function preview(payload) {
    error.textContent = '';
    try { await onPreview(payload); }
    catch { if (!disposed) error.textContent = '预览失败，请重新加载'; }
  }
  function changed() { onChange(structuredClone(draft)); }
  function heading(text) { const h = document.createElement('h3'); h.textContent = text; root.append(h); }
  heading('表情');
  const skins = document.createElement('div');
  skins.className = 'choices';
  for (const name of rendererData.skins) {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = labelFor(name, 'skin');
    button.setAttribute('aria-pressed', String(name === draft.defaultSkin));
    button.addEventListener('click', () => {
      draft.defaultSkin = name;
      for (const [value, node] of buttons) node.setAttribute('aria-pressed', String(name === value));
      changed();
      void preview({ skin: name });
    }, { signal: events.signal });
    buttons.push([name, button]);
    skins.append(button);
  }
  root.append(skins);
  const loopLabel = document.createElement('label');
  loopLabel.textContent = '循环动画';
  const select = document.createElement('select');
  for (const name of rendererData.animations) {
    const option = document.createElement('option'); option.value = name; option.textContent = labelFor(name, 'animation'); select.append(option);
  }
  select.value = draft.defaultAnimation;
  select.addEventListener('change', () => { draft.defaultAnimation = select.value; changed(); void preview({ animation: select.value }); }, { signal: events.signal });
  loopLabel.append(select);
  if (rendererData.animations.length > 1) root.append(loopLabel);
  const speedLabel = document.createElement('label');
  const speedText = document.createElement('span');
  speedText.textContent = `播放速度 · ${draft.speed.toFixed(1)}×`;
  const speed = document.createElement('input');
  Object.assign(speed, { type: 'range', min: '0.1', max: '3', step: '0.1', value: String(draft.speed) });
  speed.setAttribute('aria-label', '播放速度');
  speed.addEventListener('input', () => { draft.speed = Number(speed.value); speedText.textContent = `播放速度 · ${draft.speed.toFixed(1)}×`; changed(); void preview({ speed: draft.speed }); }, { signal: events.signal });
  speedLabel.append(speedText, speed); root.append(speedLabel);
  if (rendererData.animations.length > 1) heading('动作');
  const actions = document.createElement('div'); actions.className = 'choices';
  for (const name of rendererData.animations) {
    const button = document.createElement('button'); button.type = 'button'; button.textContent = labelFor(name, 'animation');
    button.addEventListener('click', () => { void preview({ action: name }); }, { signal: events.signal });
    actions.append(button);
  }
  if (rendererData.animations.length > 1) root.append(actions);
  const alphaLabel = document.createElement('label');
  alphaLabel.textContent = '贴图透明方式';
  const alpha = document.createElement('select');
  for (const [value, text] of [['false', '普通透明'], ['true', '预乘透明（PMA）']]) {
    const option = document.createElement('option'); option.value = value; option.textContent = text; alpha.append(option);
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
    dispose() { disposed = true; events.abort(); element.remove(); } };
}

export { mountEditor } from './studio.mjs';
