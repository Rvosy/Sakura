"""Opt-in Windows/WebView2 click-through journey. Uses isolated data and an owned underlay.

Run with bundled Python after cargo build. Temporarily moves the pointer; restores it on exit.
"""
import ctypes as C
from ctypes import wintypes as W
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[3]
U = C.windll.user32
U.SetProcessDPIAware()
U.GetAncestor.argtypes = [W.HWND, W.UINT]
U.GetAncestor.restype = W.HWND
U.WindowFromPoint.argtypes = [W.POINT]
U.WindowFromPoint.restype = W.HWND
U.GetWindowRect.argtypes = [W.HWND, C.POINTER(W.RECT)]
U.GetWindowLongPtrW.argtypes = [W.HWND, C.c_int]
U.GetWindowLongPtrW.restype = C.c_ssize_t


def underlay(root):
    # Separate process, so the check cannot accidentally rely on same-thread HTTRANSPARENT.
    callback = C.WINFUNCTYPE(C.c_ssize_t, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
    U.DefWindowProcW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
    U.DefWindowProcW.restype = C.c_ssize_t
    @callback
    def proc(hwnd, msg, wp, lp):
        if msg == 0x201:
            with (root / 'clicks').open('a') as stream: stream.write('click\n')
        return U.DefWindowProcW(hwnd, msg, wp, lp)
    class WC(C.Structure):
        _fields_ = [('style', W.UINT), ('proc', callback), ('extra', C.c_int), ('window_extra', C.c_int),
                    ('instance', W.HINSTANCE), ('icon', W.HICON), ('cursor', W.HANDLE), ('brush', W.HBRUSH),
                    ('menu', W.LPCWSTR), ('name', W.LPCWSTR)]
    wc = WC(); wc.proc = proc; wc.name = 'SakuraHitTestUnderlay'; wc.brush = W.HBRUSH(6)
    U.RegisterClassW(C.byref(wc))
    U.CreateWindowExW.argtypes = [W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD, C.c_int, C.c_int, C.c_int, C.c_int,
                                  W.HWND, W.HMENU, W.HINSTANCE, W.LPVOID]
    U.CreateWindowExW.restype = W.HWND
    hwnd = U.CreateWindowExW(0, wc.name, 'Sakura isolated click target', 0x90000000,
                             0, 0, U.GetSystemMetrics(0), U.GetSystemMetrics(1), None, None, None, None)
    (root / 'underlay.json').write_text(json.dumps({'hwnd': hwnd}))
    msg = W.MSG()
    while U.GetMessageW(C.byref(msg), None, 0, 0) > 0:
        U.TranslateMessage(C.byref(msg)); U.DispatchMessageW(C.byref(msg))


def wait_for(predicate, timeout=15):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        result = predicate()
        if result: return result
        time.sleep(0.02)
    raise AssertionError('condition did not become true')


def run():
    from playwright.sync_api import sync_playwright
    # The desktop singleton is shared even across isolated user roots. Fail before
    # launching the underlay rather than showing a dialog in an existing session.
    kernel = C.windll.kernel32
    kernel.OpenMutexW.argtypes = [W.DWORD, W.BOOL, W.LPCWSTR]
    kernel.OpenMutexW.restype = W.HANDLE
    kernel.CloseHandle.argtypes = [W.HANDLE]
    existing = kernel.OpenMutexW(0x00100000, False, r'Local\SakuraDesktop.SharedUserData.v1')
    if existing:
        kernel.CloseHandle(existing)
        raise RuntimeError('Close the running Sakura instance before this opt-in native journey.')
    root = Path(tempfile.mkdtemp(prefix='sakura-spine-native-'))
    model = ROOT / 'artifacts/spine/196104-room/spine-1'
    character = root / 'characters/probe'; character.mkdir(parents=True)
    shutil.copytree(model, character / 'model')
    (character / 'card.md').write_text('隔离的穿透验证角色', encoding='utf-8')
    (character / 'character.json').write_text(json.dumps({'id': 'probe', 'display_name': '穿透验证', 'card': 'card.md',
        'visuals': {'default': 'spine', 'resources': [{'id': 'spine', 'name': 'Spine', 'root': 'model',
                    'entry': 'spine-resource.json', 'type': 'spine.json@1'}]}}), encoding='utf-8')
    (root / 'config').mkdir()
    (root / 'config/ui.json').write_text(json.dumps({'domain': 'ui', 'schema_version': 1,
        'settings': {'first_run_guide_completed': True, 'telemetry': {'enabled': False}}}))
    (root / 'config/characters.yaml').write_text('current_character_id: probe\nvisual_selections:\n  probe: spine\n')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    env = {**os.environ, 'SAKURA_RUNTIME_USER_ROOT': str(root),
           'WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS': f'--remote-debugging-port={port}'}
    pointer = W.POINT(); U.GetCursorPos(C.byref(pointer))
    target = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--underlay', str(root)],
                               creationflags=subprocess.CREATE_NO_WINDOW)
    log = (root / 'native.log').open('w', encoding='utf-8')
    app = subprocess.Popen([str(ROOT / 'desktop/src-tauri/target/debug/sakura.exe')], cwd=ROOT, env=env,
                           stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
    print(f'isolated root: {root}', flush=True)
    try:
        import urllib.request
        def debug_ready():
            try: return json.load(urllib.request.urlopen(f'http://127.0.0.1:{port}/json/list'))
            except Exception: return False
        wait_for(debug_ready, 40)
        target_hwnd = json.loads((root / 'underlay.json').read_text())['hwnd']
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(f'http://127.0.0.1:{port}')
            page = next(p for p in browser.contexts[0].pages if '/settings/' not in p.url)
            page.wait_for_selector('#visual-renderer canvas', timeout=40000)
            status = lambda: page.evaluate("window.__TAURI__.core.invoke('dynamic_hit_test_status')")
            wait_for(status)
            # Find this isolated process's main native HWND, never another Sakura process.
            handles = []
            enum = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
            @enum
            def collect(hwnd, _):
                pid = W.DWORD(); U.GetWindowThreadProcessId(hwnd, C.byref(pid))
                if pid.value == app.pid and U.IsWindowVisible(hwnd): handles.append(hwnd)
                return True
            def visible_window():
                handles.clear(); U.EnumWindows(collect, 0)
                return handles[0] if handles else None
            hwnd = wait_for(visible_window)
            box = W.RECT(); U.GetWindowRect(hwnd, C.byref(box))
            rect = page.locator('#visual-renderer canvas').bounding_box()
            dpi = page.evaluate('devicePixelRatio')
            def move(x, y):
                px = round(box.left + (rect['x'] + x * rect['width']) * dpi)
                py = round(box.top + (rect['y'] + y * rect['height']) * dpi)
                U.SetCursorPos(px, py)
                return W.POINT(px, py)
            def ignored(): return bool(U.GetWindowLongPtrW(hwnd, -20) & 0x20)
            corner = move(0.02, 0.02)
            wait_for(ignored)
            actual = U.GetAncestor(U.WindowFromPoint(corner), 2)
            assert actual == target_hwnd, ('transparent point must reach another process', actual, target_hwnd)
            from PIL import ImageGrab
            ImageGrab.grab(bbox=(max(0, box.left), max(0, box.top), min(U.GetSystemMetrics(0), box.right),
                                min(U.GetSystemMetrics(1), box.bottom))).save(root / 'screen-transparent.png')
            # Send only to our verified test window. A real OS click must arrive there.
            U.mouse_event(2, 0, 0, 0, 0); U.mouse_event(4, 0, 0, 0, 0)
            wait_for(lambda: (root / 'clicks').exists())
            previous_clicks = (root / 'clicks').read_text().count('click')
            U.mouse_event(2, 0, 0, 0, 0)
            try:
                wait_for(lambda: (root / 'clicks').read_text().count('click') > previous_clicks)
                move(0.5, 0.5)
                # Observe the held-button interval, not a delay to make a racing assertion pass.
                end = time.monotonic() + 0.15
                while time.monotonic() < end:
                    assert ignored(), 'must not steal a drag begun in the underlying process'
            finally:
                U.mouse_event(4, 0, 0, 0, 0)
            move(0.5, 0.5)
            wait_for(lambda: not ignored())
            before = status()
            wait_for(lambda: status()['accepted'] >= before['accepted'] + 60)
            center = status()
            # Move to an owned area outside the pet. No renderer queries should be issued there.
            U.SetCursorPos(5, 5)
            wait_for(ignored)
            outside = status()
            time.sleep(0.25)
            assert status()['queries'] == outside['queries']
            assert status()['uiTicks'] == outside['uiTicks'], 'outside polling must not wake the UI thread'
            # Re-enter after ignoring mouse events, and reject the old binding on disable.
            move(0.5, 0.5); wait_for(lambda: not ignored())
            for _ in range(20):
                move(0.02, 0.02); wait_for(ignored)
                move(0.5, 0.5); wait_for(lambda: not ignored())
            report = {'center': center, 'outside': outside, 'underlyingClick': True, 'underlyingDrag': True}
            if '--benchmark' in sys.argv:
                import psutil
                def cpu_sample():
                    result = {}
                    for process in [psutil.Process(app.pid), *psutil.Process(app.pid).children(recursive=True)]:
                        try:
                            times = process.cpu_times(); result[process.pid] = times.user + times.system
                        except psutil.Error: pass
                    return result
                def measure():
                    before = cpu_sample(); start = time.perf_counter()
                    time.sleep(4)
                    after = cpu_sample(); elapsed = time.perf_counter() - start
                    cpu = sum(max(0, value - before.get(pid, value)) for pid, value in after.items()) / elapsed
                    return {'oneCorePercent': cpu * 100, 'machinePercent': cpu * 100 / psutil.cpu_count(), 'seconds': elapsed}
                measurements = {}
                measurements['enabledInside'] = measure()
                U.SetCursorPos(5, 5); wait_for(ignored)
                measurements['enabledOutside'] = measure()
                session = status()['session']
                page.evaluate("session => window.__TAURI__.core.invoke('configure_dynamic_hit_test', {session, enabled:false})", session)
                move(0.5, 0.5)
                measurements['disabledInside'] = measure()
                report['cpu'] = measurements
            (root / 'result.json').write_text(json.dumps(report, indent=2))
            print(json.dumps(report, indent=2), flush=True)
            page.screenshot(path=str(root / 'webview.png'), omit_background=True)
            browser.close()
    finally:
        app.terminate(); app.wait(timeout=15)
        target.terminate(); target.wait(timeout=15)
        U.SetCursorPos(pointer.x, pointer.y); log.close()


if __name__ == '__main__':
    if '--underlay' in sys.argv: underlay(Path(sys.argv[-1]))
    else: run()
