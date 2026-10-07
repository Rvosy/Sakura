"""插件拥有的本机 SakuraTTS 服务及运行检查。"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request

from sakura_http import urlopen_direct_for_loopback
from sakura_process import terminate_process_tree
try:
    from ._bundle import Cancelled
    from ._diagnostics import nvidia_device, operation
except ImportError:
    from _bundle import Cancelled
    from _diagnostics import nvidia_device, operation


def command(root, release, *args):
    return [str(root / release.get('python_executable', 'runtime/main/python.exe')),
            '-I', '-X', 'utf8', str(root / 'launcher.py'), *args]


def environment(cache):
    return dict(os.environ, SAKURATTS_CACHE_DIR=str(cache))


def runtime_key(bundle, config, voice):
    return (bundle[0], config['backend'], config['cudaProfile'] if config['backend'] == 'cuda' else None,
            config['idleSeconds'], voice['gpt'], voice['sovits'])


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
    def __init__(self, directory, log=lambda *_args, **_fields: None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.process = None
        self.key = None
        self.url = ''
        self.backend = ''
        self.profile = None
        self.mode = 'managed'
        self.selection_reason = ''
        self.emit = log
        self.observer = None
        self.observer_stop = threading.Event()
        self.device = {}
        self.device_checked = False

    def stop(self):
        with self.lock:
            self.observer_stop.set()
            if self.observer is not None:
                self.observer.join()
                self.observer = None
            if self.process is not None:
                terminate_process_tree(self.process, timeout=5)
                self.process = None
                self.emit('info', 'SakuraTTS 服务已停止', event='tts.engine.stopped', backend=self.backend)
            self.key = None

    def start(self, bundle, config, voice, cancel):
        key = runtime_key(bundle, config, voice)
        with self.lock:
            if cancel.is_set():
                raise Cancelled()
            if self.key == key and self.process is not None and self.process.poll() is None:
                return
        if not self.device_checked:
            self.device = nvidia_device()
            self.device_checked = True
        with operation(self.emit, 'engine_start', cancel=cancel, report_error=False):
            self._start(bundle, config, voice, cancel, key)

    def can_synthesize_in_background(self, bundle, config, voice):
        with self.lock:
            if self.key != runtime_key(bundle, config, voice):
                return False
        return self.status() in {'awake', 'ready'}

    def _start(self, bundle, config, voice, cancel, key):
        started = time.monotonic()
        root, release = bundle
        backend = config['backend']
        profile = config['cudaProfile'] if backend == 'cuda' else None
        with self.lock:
            self.stop()
        self.backend, self.profile = '', None
        self.selection_reason = ''
        if backend == 'auto':
            backend = release['backend']
            if 'cuda' in release['backends']:
                try:
                    probe(root, release, cancel, 'cuda', self.directory / 'cache')
                    backend = 'cuda'
                except RuntimeError as error:
                    backend = 'cpu'
                    self.selection_reason = 'NVIDIA 检查未通过，使用 CPU。'
                    self.emit('warning', self.selection_reason, stage='backend_check', diagnostic=str(error))
        if backend not in release['backends']:
            raise ValueError('此整合包不支持所选推理后端。')
        with self.lock:
            if cancel.is_set():
                raise Cancelled()
            self.emit('info', 'SakuraTTS 正在启动服务', event='tts.engine.starting', backend=backend, profile=profile,
                      gpt_model=Path(voice['gpt']).name, sovits_model=Path(voice['sovits']).name)
            settings = self.directory / 'inference.json'
            settings.write_text(json.dumps({'custom': {'t2s_weights_path': voice['gpt'],
                'vits_weights_path': voice['sovits'], 'is_half': False},
                'sakuratts': {'backend': backend, **({'profile': profile} if profile else {})}},
                ensure_ascii=False), encoding='utf-8')
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            self.url = f'http://127.0.0.1:{port}'
            self.backend = backend
            self.profile = profile
            self.log = self.directory / 'server.log'
            self.mode = 'direct' if config['idleSeconds'] == 0 else 'managed'
            sleep_args = ['--idle-sleep-seconds', str(config['idleSeconds'])] if self.mode == 'managed' else []
            with self.log.open('wb') as output:
                self.process = subprocess.Popen(command(root, release, 'serve', '--runtime-mode', self.mode,
                    *sleep_args, '--backend', backend,
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
                    self.emit('info', 'SakuraTTS 服务已就绪', event='tts.engine.ready', backend=backend,
                              elapsed_ms=round((time.monotonic() - started) * 1000, 1))
                    if self.mode == 'managed':
                        self.observer_stop.clear()
                        self.observer = threading.Thread(target=self._observe, args=(process,), daemon=True,
                                                         name='sakuratts-state')
                        self.observer.start()
                    return
                except (URLError, TimeoutError, ConnectionError):
                    if time.monotonic() >= deadline:
                        raise TimeoutError('SakuraTTS 服务在 120 秒内未就绪。')
                    cancel.wait(.1)
        except BaseException:
            self.stop()
            raise

    def _observe(self, process):
        previous = None
        previous_error = None
        labels = {'waking': 'SakuraTTS 正在加载模型', 'awake': 'SakuraTTS 模型已加载',
                  'stopping': 'SakuraTTS 正在释放模型', 'sleeping': 'SakuraTTS 已休眠',
                  'failed': 'SakuraTTS 引擎运行失败'}
        while not self.observer_stop.is_set() and process.poll() is None:
            try:
                snapshot = self.request('/runtime', timeout=1)
            except (URLError, TimeoutError, ConnectionError, RuntimeError) as error:
                diagnostic = str(error)
                if not self.observer_stop.is_set() and diagnostic != previous_error:
                    self.emit('warning', 'SakuraTTS 状态读取失败', diagnostic=diagnostic,
                              event='tts.engine.observation_failed')
                previous_error = diagnostic
                if self.observer_stop.wait(1):
                    return
                continue
            previous_error = None
            state = snapshot.get('state')
            if self.observer_stop.is_set():
                return
            if state != previous and state in labels:
                self.emit('error' if state == 'failed' else 'info', labels[state],
                          event='tts.engine.' + state, backend=self.backend,
                          **({'diagnostic': snapshot.get('last_error')} if state == 'failed' else {}))
            previous = state
            if self.observer_stop.wait(1):
                return

    def request(self, path, data=None, timeout=300):
        started = time.monotonic()
        request = Request(self.url + path, data=None if data is None else json.dumps(data).encode(),
                          headers={'Content-Type': 'application/json'})
        try:
            with urlopen_direct_for_loopback(request, timeout=timeout) as response:
                body = response.read()
                if path == '/tts':
                    fields = {'backend': self.backend, 'profile': self.profile,
                              'elapsed_ms': round((time.monotonic() - started) * 1000, 1),
                              'bytes': len(body)}
                    for suffix, key in [('Total-Ms', 'engine_total_ms'), ('Request-Ms', 'inference_ms'),
                                        ('Reference-Ms', 'reference_ms'), ('Frontend-Ms', 'frontend_ms'),
                                        ('Semantic-Ms', 'gpt_ms'), ('Acoustic-Ms', 'sovits_ms'),
                                        ('Audio-Seconds', 'audio_seconds')]:
                        value = response.headers.get('X-SakuraTTS-' + suffix)
                        if value is not None:
                            try:
                                number = float(value)
                            except ValueError:
                                continue
                            if math.isfinite(number) and number >= 0:
                                fields[key] = round(number, 3)
                    cache = response.headers.get('X-SakuraTTS-Reference-Cache')
                    if cache in {'memory', 'disk', 'package', 'miss'}:
                        fields['reference_cache'] = cache
                    if fields.get('audio_seconds'):
                        fields['rtf'] = round(fields['elapsed_ms'] / 1000 / fields['audio_seconds'], 3)
                    self.emit('info', 'SakuraTTS 合成耗时', **fields)
                return body if path == '/tts' else json.loads(body)
        except HTTPError as error:
            with error:
                body = error.read().decode('utf-8', errors='replace')
            try:
                failure = json.loads(body)
            except ValueError:
                detail = body.strip()
            else:
                detail = '；'.join(str(failure[key]) for key in ('message', 'Exception') if failure.get(key)) if isinstance(failure, dict) else str(failure)
            raise RuntimeError(f'SakuraTTS HTTP {error.code}：{detail or error.reason}') from error

    def status(self):
        with self.lock:
            alive = self.process is not None and self.process.poll() is None
        if not alive:
            return 'stopped'
        try:
            if self.mode == 'direct':
                return self.request('/health', timeout=1)['status']
            state = self.request('/runtime', timeout=1)
            return 'busy' if state.get('busy') else state.get('state', 'running')
        except (URLError, TimeoutError, ConnectionError):
            return 'connecting'
