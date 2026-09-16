const frames = [...document.querySelectorAll('iframe')];
const scenario = document.querySelector('#scenario');
let mode = 'proposed';
const notes = {
  import: '三个角色导入故障在当前链路中得到同一个 CHARACTER_IMPORT_FAILED。改造版分别保留文件缺失、ZIP 损坏和 Windows 权限错误。',
  api: '已有的模型诊断在当前版仍然保留于详情中；改造版将原文直接放到列表中，并补足限流和超时的请求信息。',
  tts: '比较启动进程、加载权重和保存音频三个阶段；改造版保留底层系统错误及相对资源路径。',
  plugins: '安装失败后又发生回滚失败时，同时保留两个错误。可在日志页切换插件筛选。',
  mixed: '包含配置写入失败、独立前端异常、超长服务错误、未捕获到原始原因，以及日志文件写入失败。详情中的代码位置和版本均为模拟值。',
};
function showMode() {
  document.body.dataset.mode = mode;
  document.querySelector('#closed').hidden = true;
  document.querySelector('.windows').hidden = false;
  document.querySelector('#current-window').hidden = mode === 'proposed';
  document.querySelector('#proposed-window').hidden = mode === 'current';
  for (const button of document.querySelectorAll('[data-mode]')) button.setAttribute('aria-pressed', String(button.dataset.mode === mode));
  document.querySelector('#mode-note').textContent = mode === 'current' ? '原页面结构、样式与交互' : mode === 'proposed' ? '原始原因直接可见 · 展开诊断详情' : '同一组故障 · 左旧右新';
}
for (const button of document.querySelectorAll('[data-mode]')) button.addEventListener('click', () => { mode = button.dataset.mode; showMode(); });
scenario.addEventListener('change', () => {
  for (const frame of frames) frame.src = `./viewer.html?mode=${frame.id}&scenario=${scenario.value}`;
  document.querySelector('#scenario-note').textContent = notes[scenario.value];
  showMode();
});
window.addEventListener('message', event => {
  if (event.origin !== location.origin || !frames.some(frame => frame.contentWindow === event.source)) return;
  if (event.data?.type === 'demo-close') {
    event.source.frameElement.parentElement.hidden = true;
    if (mode !== 'compare' || [...document.querySelectorAll('.window')].every(element => element.hidden)) {
      document.querySelector('.windows').hidden = true;
      document.querySelector('#closed').hidden = false;
    }
  }
  if (event.data?.type === 'demo-copy' && typeof event.data.text === 'string') {
    document.querySelector('#copy-preview').value = event.data.text;
    document.querySelector('#copy-status').textContent = '';
    document.querySelector('#copy-dialog').showModal();
  }
});
document.querySelector('#reopen').addEventListener('click', showMode);
document.querySelector('#dismiss-copy').addEventListener('click', () => document.querySelector('#copy-dialog').close());
document.querySelector('#copy-report').addEventListener('click', async () => {
  try { await navigator.clipboard.writeText(document.querySelector('#copy-preview').value); document.querySelector('#copy-status').textContent = '已复制。'; }
  catch { document.querySelector('#copy-preview').select(); document.querySelector('#copy-status').textContent = '请按 Ctrl+C 复制选中的内容。'; }
});
document.querySelector('#scenario-note').textContent = notes[scenario.value];
showMode();
