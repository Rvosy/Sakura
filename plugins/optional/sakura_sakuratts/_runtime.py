"""插件拥有的本机 SakuraTTS 服务及运行检查。"""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
from urllib.error import URLError
from urllib.request import Request

from sakura_http import urlopen_direct_for_loopback
from sakura_process import terminate_process_tree
try:
    from ._bundle import Cancelled
except ImportError:
    from _bundle import Cancelled


def command(root, release, *args):
    return [str(root / release.get('python_executable', 'runtime/main/python.exe')),
            '-I', '-X', 'utf8', str(root / 'launcher.py'), *args]


def environment(cache):
    return dict(os.environ, SAKURATTS_CACHE_DIR=str(cache))


def probe(root, release, cancel, backend=None, cache=None):
    backend = backend or release['backend']
    log = root / 'logs/plugin-check.log'
    log.parent.mkdir(exist_ok=True)
    with log.open('wb') as output:
        process = subprocess.Popen(command(root, release, 'check-runtime', '--backend', backend),
                                   cwd=root, env=environment(cache or root / 'cache'),
                                   stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                                   start_new_session=os.name != 'nt')
        try:
            deadline = time.monotonic() + 120
            while process.poll() is None:
                if cancel.wait(.1):
                    raise Cancelled()
                if time.monotonic() >= deadline:
                    raise TimeoutError('运行环境检查超过 120 秒。')
            if process.returncode:
                raise RuntimeError(log.read_text(encoding='utf-8', errors='replace')[-4000:])
        finally:
            terminate_process_tree(process, timeout=5)


class Runtime:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.process = None
        self.key = None
        self.url = ''
        self.backend = ''
        self.selection_reason = ''
        self.auto_selection = None

    def stop(self):
        with self.lock:
            if self.process is not None:
                terminate_process_tree(self.process, timeout=5)
                self.process = None
            self.key = None

    def start(self, bundle, config, voice, cancel):
        root, release = bundle
        backend = config['backend']
        if backend == 'auto':
            if not self.auto_selection or self.auto_selection[0] != root:
                selected = release['backend']
                self.selection_reason = ''
                if 'cuda' in release['backends']:
                    try:
                        probe(root, release, cancel, 'cuda', self.directory / 'cache')
                        selected = 'cuda'
                    except Cancelled:
                        raise
                    except Exception as error:
                        selected = 'cpu'
                        self.selection_reason = 'NVIDIA 检查未通过，使用 CPU。'
                        (self.directory / 'backend-check.log').write_text(str(error), encoding='utf-8')
                self.auto_selection = root, selected
            backend = self.auto_selection[1]
        if backend not in release['backends']:
            raise ValueError('此整合包不支持所选推理后端。')
        key = (root, backend, config['idleSeconds'], voice['gpt'], voice['sovits'])
        with self.lock:
            if cancel.is_set():
                raise Cancelled()
            if self.key == key and self.process is not None and self.process.poll() is None:
                return
            self.stop()
            settings = self.directory / 'inference.json'
            settings.write_text(json.dumps({'custom': {'t2s_weights_path': voice['gpt'],
                'vits_weights_path': voice['sovits'], 'is_half': False},
                'sakuratts': {'backend': backend}}, ensure_ascii=False), encoding='utf-8')
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            self.url = f'http://127.0.0.1:{port}'
            self.backend = backend
            self.log = self.directory / 'server.log'
            with self.log.open('wb') as output:
                self.process = subprocess.Popen(command(root, release, 'serve', '--runtime-mode', 'managed',
                    '--idle-sleep-seconds', str(config['idleSeconds']), '--backend', backend,
                    '-c', str(settings), '-a', '127.0.0.1', '-p', str(port)), cwd=root,
                    env=environment(self.directory / 'cache'), stdin=subprocess.DEVNULL,
                    stdout=output, stderr=subprocess.STDOUT, start_new_session=os.name != 'nt')
            self.key = key
            process = self.process
        deadline = time.monotonic() + 120
        try:
            while True:
                if cancel.is_set():
                    raise Cancelled()
                if process.poll() is not None:
                    raise RuntimeError(self.log.read_text(encoding='utf-8', errors='replace')[-4000:])
                try:
                    self.request('/health', timeout=1)
                    return
                except (URLError, TimeoutError, ConnectionError):
                    if time.monotonic() >= deadline:
                        raise TimeoutError('SakuraTTS 服务在 120 秒内未就绪。')
                    cancel.wait(.1)
        except BaseException:
            self.stop()
            raise

    def request(self, path, data=None, timeout=300):
        request = Request(self.url + path, data=None if data is None else json.dumps(data).encode(),
                          headers={'Content-Type': 'application/json'})
        with urlopen_direct_for_loopback(request, timeout=timeout) as response:
            body = response.read()
            return body if path == '/tts' else json.loads(body)

    def status(self):
        with self.lock:
            alive = self.process is not None and self.process.poll() is None
        if not alive:
            return '未启动'
        try:
            state = self.request('/runtime', timeout=1)
            return {'sleeping': '已休眠', 'awake': '就绪', 'failed': '启动失败', 'stopping': '正在休眠', 'ready': '就绪', 'waking': '正在唤醒',
                    'preparing': '正在准备', 'busy': '正在合成'}.get(state.get('state'), state.get('state', '运行中'))
        except (URLError, TimeoutError, ConnectionError):
            return '连接中'
