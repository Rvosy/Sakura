import assert from 'node:assert/strict';
// Opt-in Windows/WebView2 regression, outside the platform-independent node:test suite.
// Start a debug build with an ISOLATED SAKURA_RUNTIME_USER_ROOT containing a valid character,
// and WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=<port>.
// Then run: node desktop/frontend/tests/windows-layout-preview.journey.mjs <port>
// The Settings window must be closed before the run. No settings are saved; successful runs
// cancel the preview. If an old build deadlocks, stop that isolated process and keep its logs.
const port = Number(process.argv[2]);
assert.ok(Number.isInteger(port) && port > 0 && port < 65536, 'pass the isolated WebView2 debugging port');
const pause = ms => new Promise(r => setTimeout(r, ms));
const sockets = [];
async function pages() { return (await fetch(`http://127.0.0.1:${port}/json/list`)).json(); }
async function connect(page) {
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  sockets.push(ws);
  await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
  let sequence = 0;
  const pending = new Map();
  ws.onmessage = event => {
    const value = JSON.parse(event.data);
    pending.get(value.id)?.(value);
  };
  return async expression => {
    const id = ++sequence;
    let timer;
    try {
      const value = await new Promise((resolve, reject) => {
        pending.set(id, resolve);
        timer = setTimeout(() => reject(Error(`WebView response timeout: ${expression.slice(0, 100)}`)), 5000);
        ws.send(JSON.stringify({id, method:'Runtime.evaluate', params:{expression, awaitPromise:true, returnByValue:true}}));
      });
      if (value.error || value.result?.exceptionDetails) throw Error(JSON.stringify(value));
      return value.result?.result?.value;
    } finally { clearTimeout(timer); pending.delete(id); }
  };
}
try {
  const initialPages = await pages();
  assert.ok(!initialPages.some(p => p.url.includes('/settings/')), 'close Settings before the isolated test');
  const mainPage = initialPages.find(p => p.title === 'Sakura Runtime v2');
  assert.ok(mainPage, 'isolated Sakura main WebView exists');
  const main = await connect(mainPage);
  await main(`window.__TAURI__.core.invoke('activate_pet_context_menu_action', {actionId:'sakura.settings.open'})`);
  let settingsPage;
  for (let i = 0; i < 80 && !settingsPage; i++) {
    settingsPage = (await pages()).find(p => p.url.includes('/settings/'));
    if (!settingsPage) await pause(100);
  }
  assert.ok(settingsPage, 'settings WebView opens');
  const settings = await connect(settingsPage);
  for (let i = 0; i < 80; i++) {
    if (await settings(`Boolean(document.getElementById('bubbleHeight') && !document.getElementById('bubbleHeight').disabled)`)) break;
    await pause(100);
  }
  assert.equal(await settings(`document.getElementById('bubbleHeight').disabled`), false);
  const cases = [['controlPanelWidth', 620, 740], ['bubbleHeight', 180, 300], ['controlPanelOffset', -100, 120], ['inputBarOffset', 40, 130]];
  for (let cycle = 0; cycle < 20; cycle++) {
    for (const [id, a, b] of cases) {
      const value = cycle % 2 ? a : b;
      await settings(`(async () => {
        const slider = document.getElementById(${JSON.stringify(id)});
        slider.dispatchEvent(new PointerEvent('pointerdown', {bubbles:true}));
        slider.focus();
        slider.value = ${value};
        slider.dispatchEvent(new Event('input', {bubbles:true}));
        await new Promise(r => requestAnimationFrame(r));
        slider.dispatchEvent(new PointerEvent('pointerup', {bubbles:true}));
      })()`);
      // Compete with final native settlement just as the next slider begins.
      await pause(10);
    }
    let settled = false;
    for (let i = 0; i < 100; i++) {
      settled = await main(`!document.getElementById('pet-stage').dataset.layoutPreview`);
      if (settled) break;
      await pause(20);
    }
    assert.ok(settled, `cycle ${cycle}: native preview settles`);
    await settings(`window.__TAURI__.core.invoke('runtime_lifecycle_snapshot')`);
    console.log(`cycle ${cycle + 1}: four sliders and native settlement responsive`);
  }
  await settings(`document.getElementById('cancelButton').click()`);
  console.log('PASS: 80 layout changes through real WebView/Tauri/Win32');
} finally { for (const ws of sockets) ws.close(); }
