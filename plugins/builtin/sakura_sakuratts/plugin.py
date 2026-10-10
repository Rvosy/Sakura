from __future__ import annotations
from sakura_provider_errors import provider_failure

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading
import time
import uuid
from urllib.parse import urlencode

try:
    from ._bundle import BundleStore, Cancelled
    from ._runtime import Runtime, probe
    from ._updates import UpdateAnnouncement
    from ._diagnostics import operation
except ImportError:
    from _bundle import BundleStore, Cancelled
    from _runtime import Runtime, probe
    from _updates import UpdateAnnouncement
    from _diagnostics import operation

PROVIDER_ID = 'sakura.tts.sakuratts'
SERVICE_KEY = 'sakura.tts.provider.sakuratts'
DEFAULTS = {'backend': 'auto', 'cudaProfile': 'fp16', 'idleSeconds': 60,
            'autoCheckUpdates': True}
CUDA_PROFILES = [('fp16', 'FP16 标准'), ('fp32', 'FP32 全精度'),
                 ('low-memory', 'FP16 低显存'), ('minimum-memory', 'FP16 极低显存')]


def configuration(values):
    result = {key: values.get(key, value) for key, value in DEFAULTS.items()}
    if result['backend'] not in {'auto', 'cpu', 'cuda', 'directml', 'mlx'}:
        raise ValueError('不支持此推理后端。')
    if result['cudaProfile'] not in dict(CUDA_PROFILES):
        raise ValueError('不支持此 NVIDIA 推理档位。')
    if type(result['idleSeconds']) is not int or result['idleSeconds'] < 0:
        raise ValueError('空闲休眠时间必须是非负整数。')
    if type(result['autoCheckUpdates']) is not bool:
        raise ValueError('自动检查更新设置无效。')
    return result


def character_voice(character, character_id, tone='中性'):
    manifest_path = character.resolve_resource(character_id, 'character.json')
    manifest = json.loads(Path(manifest_path).read_text(encoding='utf-8'))
    extension = dict(manifest.get('extensions', {}).get('sakura.tts.gpt-sovits', {}))
    extension.update(character.get(character_id))
    for key, label in (('toneRefs', '参考音频表'), ('gptModel', 'GPT 模型'), ('sovitsModel', 'SoVITS 模型')):
        if not extension.get(key):
            raise ValueError(f'角色尚未配置{label}，请在角色工坊中添加。')
    package_dir = Path(manifest_path).parent
    resources = {key: Path(character.resolve_resource(character_id, extension[key]))
                 for key in ('toneRefs', 'gptModel', 'sovitsModel')}
    references = []
    for line in resources['toneRefs'].read_text(encoding='utf-8-sig').splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        parts = [part.strip() for part in line.split('|')]
        if len(parts) != 4 or not all(parts):
            raise ValueError('角色参考音频配置无效。')
        references.append(parts)
    if not references:
        raise ValueError('角色尚未配置参考音频。')
    reference = next((r for r in references if r[3] == tone),
                     next((r for r in references if r[3] == '中性'), references[0]))
    reference_paths = {row[0]: Path(character.resolve_resource(character_id, row[0])) for row in references}
    character.declare_resources(character_id, {'kind': 'tts',
        'paths': [path.relative_to(package_dir).as_posix()
                  for path in (*resources.values(), *reference_paths.values())],
        'pluginRequirements': [{'kind': 'tts', 'type': 'gpt-sovits.models@1',
            'plugins': [{'id': PROVIDER_ID, 'name': 'SakuraTTS'}]}]})
    return {'gpt': str(resources['gptModel']), 'sovits': str(resources['sovitsModel']),
            'ref_audio_path': str(reference_paths[reference[0]]),
            'prompt_lang': reference[1].lower(), 'prompt_text': reference[2],
            'text_lang': extension.get('textLang', 'ja')}


class Provider:
    def __init__(self, context):
        self.context = context
        self.character = context.get('sakura.host.character')
        self.artifacts = context.get('sakura.host.artifacts')
        self.hub = context.get('sakura.tts')
        self.logger = context.get('sakura.host.logging')
        self.directory = Path(context.data_path('.'))
        self.runtime = Runtime(self.directory / 'runtime-data', self.log)
        self.config = configuration(context.config.get())
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='sakuratts')
        self.lock = threading.RLock()
        self.jobs = {}
        self.closed = False
        self.active = None
        self.wake = None
        self.error = ''
        self.bundle = None
        self.bundle = BundleStore(self.directory / 'bundles', probe, self.publish, self.log)

    def log(self, level, message, **fields):
        bundle = self.bundle.current() if self.bundle is not None else None
        release = bundle[1] if bundle else {}
        context = {'provider_id': PROVIDER_ID, 'requested_backend': self.config['backend'],
                   'backend': self.runtime.backend or None, 'profile': self.runtime.profile,
                   'runtime_mode': 'direct' if self.config['idleSeconds'] == 0 else 'managed',
                   'bundle_version': (self.bundle.release_id or release.get('version')) if bundle else None,
                   'bundle_source_commit': release.get('source_commit'), **self.runtime.device}
        getattr(self.logger, level)(message, fields={key: value for key, value in {**context, **fields}.items() if value is not None})

    def publish(self, activate):
        def switch():
            self.runtime.stop()
            activate()
            self.error = ''
        self.executor.submit(switch).result()

    def engine_state(self):
        if not self.bundle.current():
            if self.bundle.installation_error:
                return {'state': 'error', 'label': '运行环境不可用', 'message': self.bundle.installation_error[-240:]}
            return {'state': 'warning', 'label': '未安装运行环境', 'message': '请先导入整合包。'}
        if self.error:
            return {'state': 'error', 'label': '语音运行失败', 'message': self.error[-240:]}
        try:
            status = self.runtime.status()
        except RuntimeError as error:
            return {'state': 'error', 'label': '状态读取失败', 'message': str(error)}
        if status == 'stopped':
            if self.active is not None or self.wake is not None:
                return {'state': 'working', 'label': '正在加载', 'message': ''}
            return {'state': 'ready', 'label': '未加载', 'message': '首次合成时自动加载模型。'}
        state, label = {
            'sleeping': ('ready', '已休眠'), 'awake': ('ready', '已加载'), 'ready': ('ready', '已加载'),
            'connecting': ('working', '连接中'), 'waking': ('working', '正在加载'),
            'preparing': ('working', '正在准备'), 'busy': ('working', '正在合成'),
            'stopping': ('working', '正在休眠'), 'failed': ('error', '启动失败'),
            'running': ('ready', '运行中'),
        }.get(status, ('ready', status))
        message = '模型资源已释放，下次合成自动加载。' if status == 'sleeping' else self.runtime.selection_reason
        backend_label = {'directml': 'AMD / Intel 显卡'}.get(self.runtime.backend, self.runtime.backend.upper())
        return {'state': state,
                'label': label + (f' · {backend_label}' if backend_label else ''),
                'message': message}

    def status(self):
        available = self.bundle.current() is not None
        return {'label': 'SakuraTTS', 'available': available,
                'reasonCode': 'READY' if available else 'TTS_RUNTIME_NOT_INSTALLED', 'stage': 'runtime_configuration'}

    def warmup(self, character_id):
        # 对话事件负责提前唤醒；应用启动和设置刷新不加载模型。
        return False

    def on_chat(self, event):
        character_id = event['characterId']
        selected = self.hub.status(character_id)
        with self.lock:
            if self.closed or not selected['enabled'] or selected['providerId'] != PROVIDER_ID:
                return
            if self.wake is not None or not self.bundle.current():
                return
            cancel = threading.Event()
            self.wake = cancel
            self.executor.submit(self._prewake, character_id, cancel)

    def _prewake(self, character_id, cancel):
        started = time.monotonic()
        try:
            with operation(self.log, 'preload', cancel=cancel):
                self.error = ''
                voice = character_voice(self.character, character_id)
                self.runtime.start(self.bundle.current(), self.config, voice, cancel)
                if not cancel.is_set():
                    self.log('info', 'SakuraTTS 正在提前加载模型', backend=self.runtime.backend)
                    if self.config['idleSeconds'] > 0:
                        self.runtime.request('/runtime/wake', {'keep_alive_seconds': 0})
                    self.log('info', 'SakuraTTS 正在准备默认参考音频', reference_audio=Path(voice['ref_audio_path']).name)
                    self.runtime.request('/set_refer_audio?' + urlencode({'refer_audio_path': voice['ref_audio_path']}))
                    self.log('info', 'SakuraTTS 模型与默认参考音频已就绪', backend=self.runtime.backend,
                             elapsed_ms=round((time.monotonic() - started) * 1000, 1))
        except Cancelled:
            pass
        except Exception as error:
            self.error = str(error)
        finally:
            with self.lock:
                if self.wake is cancel:
                    self.wake = None

    def record_error(self, error):
        self.error = str(error)
        self.log('error', 'SakuraTTS 运行失败', diagnostic=self.error, error_type=type(error).__name__)

    def begin(self, request):
        with self.lock:
            if self.closed or not self.bundle.current():
                return {'errorCode': 'TTS_RUNTIME_NOT_INSTALLED'}
            try:
                self.error = ''
                voice = character_voice(self.character, request['characterId'], request.get('options', {}).get('tone', '中性'))
            except Exception as error:
                self.record_error(error)
                return provider_failure('TTS_CHARACTER_CONFIG_INVALID', error)
            allocation = self.artifacts.allocate({'mediaType': 'audio/wav', 'suffix': '.wav'})
            job_id = 'job_' + uuid.uuid4().hex
            job = {'state': 'running', 'cancel': threading.Event(), 'allocation': allocation, 'released': False,
                   'queued_at': time.monotonic()}
            self.jobs[job_id] = job
            self.executor.submit(self._synthesize, job, request, voice)
            return job_id

    def _release(self, job):
        if not job['released']:
            self.artifacts.release(job['allocation']['artifactId'])
            job['released'] = True

    def _synthesize(self, job, request, voice):
        try:
            background = request.get('options', {}).get('background', False)
            if (background
                    and not self.runtime.can_synthesize_in_background(self.bundle.current(), self.config, voice)):
                with self.lock:
                    if job['cancel'].is_set():
                        raise Cancelled()
                    job['failure'] = {'errorCode': 'TTS_BACKGROUND_DEFERRED'}
                    job['state'] = 'failed'
                return
            with operation(self.log, 'synthesis', cancel=job['cancel']):
                with self.lock:
                    if job['cancel'].is_set():
                        raise Cancelled()
                    self.active = job
                queue_ms = round((time.monotonic() - job['queued_at']) * 1000, 1)
                if not background:
                    self.runtime.start(self.bundle.current(), self.config, voice, job['cancel'])
                payload = {key: voice[key] for key in ('text_lang', 'ref_audio_path', 'prompt_lang', 'prompt_text')}
                payload.update(text=request['text'], parallel_infer=False, streaming_mode=False, media_type='wav')
                self.log('info', 'SakuraTTS 开始合成', backend=self.runtime.backend, text_chars=len(request['text']),
                         reference_audio=Path(voice['ref_audio_path']).name, language=voice['text_lang'], queue_ms=queue_ms)
                audio = self.runtime.request('/tts', payload)
                with self.lock:
                    if job['cancel'].is_set():
                        raise Cancelled()
                    Path(job['allocation']['path']).write_bytes(audio)
                    job['state'] = 'succeeded'
        except Cancelled:
            self.log('info', 'SakuraTTS 合成已取消')
            with self.lock:
                job['state'] = 'cancelled'
        except Exception as error:
            with self.lock:
                job['failure'] = provider_failure('TTS_SYNTHESIS_FAILED', error)
                job['state'] = 'cancelled' if job['cancel'].is_set() else 'failed'
            if job['state'] == 'cancelled':
                self.log('info', 'SakuraTTS 合成已取消')
            else:
                self.error = str(error)
        finally:
            with self.lock:
                if self.active is job:
                    self.active = None
                if job['state'] != 'succeeded':
                    self._release(job)

    def poll(self, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None:
                return {'state': 'failed', 'errorCode': 'TTS_JOB_NOT_FOUND'}
            state = job['state']
            if state == 'running':
                return {'state': state}
            try:
                if state == 'succeeded':
                    try:
                        return {'state': state, 'artifact': self.artifacts.commit(job['allocation']['artifactId'])}
                    except Exception as error:
                        self.record_error(error)
                        return {'state': 'failed', **provider_failure('TTS_ARTIFACT_INVALID', error)}
                return {'state': state, **(job['failure'] if state == 'failed' else {})}
            finally:
                self._release(job)
                del self.jobs[job_id]

    def cancel(self, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None or job['state'] != 'running':
                return False
            job['cancel'].set()
            if self.active is job:
                self.runtime.stop()
            return True

    def reconfigure(self, values):
        config = configuration(values)
        with self.lock:
            if (config['backend'] != self.config['backend'] or config['idleSeconds'] != self.config['idleSeconds']
                    or (config['backend'] == 'cuda' and config['cudaProfile'] != self.config['cudaProfile'])):
                self.cancel_work()
                self.runtime.stop()
            self.config = config
        return 'applied'

    def cancel_work(self):
        for job in self.jobs.values():
            if job['state'] == 'running':
                job['cancel'].set()
        if self.wake is not None:
            self.wake.set()

    def close(self):
        with self.lock:
            self.closed = True
            self.cancel_work()
            self.runtime.stop()
        self.bundle.close()
        self.executor.shutdown(wait=True)
        with self.lock:
            for job in self.jobs.values():
                self._release(job)
            self.jobs.clear()


class SakuraTTSPlugin:
    def setup(self, context):
        provider = Provider(context)
        context.effect(provider.close)
        context.provide(SERVICE_KEY, provider, exports=('status', 'warmup', 'begin', 'poll', 'cancel'))
        provider.hub.registerProvider({'providerId': PROVIDER_ID, 'serviceKey': SERVICE_KEY, 'label': 'SakuraTTS'})
        context.effect(lambda: provider.hub.unregisterProvider(PROVIDER_ID, SERVICE_KEY))
        context.config.on_change(provider.reconfigure)
        context.on('sakura.host.chat.request.started', provider.on_chat)
        settings = context.get('sakura.host.settings')
        surface = context.get('sakura.host.settings.surface-v0')
        settings.register({'sectionId': 'overview', 'title': '运行状态', 'order': 10, 'fields': [
            {'key': 'engineState', 'label': '语音引擎', 'type': 'status', 'placement': 'row',
             'default': provider.engine_state()},
        ]}, load=lambda: {'engineState': provider.engine_state()}, save=lambda values: None)
        surface.register('overview', 'plugin')
        import platform
        options = [('auto', '自动'), ('mlx', 'Apple GPU（MLX）')] if platform.system() == 'Darwin' else [
            ('auto', '自动'), ('cpu', 'CPU'), ('cuda', 'NVIDIA（英伟达）'), ('directml', 'AMD / Intel 显卡')]
        settings.register({'sectionId': 'runtime', 'title': '运行设置', 'order': 20, 'fields': [
            {'key': 'backend', 'label': '运行设备', 'type': 'select', 'default': 'auto',
             'options': [{'value': value, 'label': label} for value, label in options]},
            {'key': 'cudaProfile', 'label': '推理模式', 'type': 'select', 'default': DEFAULTS['cudaProfile'],
             'options': [{'value': value, 'label': label} for value, label in CUDA_PROFILES],
             'enabledWhen': {'field': 'backend', 'equals': 'cuda', 'hide': True},
             'description': '低显存档通过分阶段加载模型节省显存，可能增加耗时。首次使用 FP16 需要转换模型。'},
            {'key': 'idleSeconds', 'label': '空闲后休眠（秒）', 'type': 'integer', 'minimum': 0,
             'default': DEFAULTS['idleSeconds'], 'description': '0 为不休眠；启用自动休眠时暂停后台语音补齐。'},
            {'key': 'autoCheckUpdates', 'label': '启动时检查整合包更新', 'type': 'boolean', 'default': True},
        ]}, load=lambda: provider.config, save=context.config.update)
        surface.register('runtime', 'plugin')
        settings.register({'sectionId': 'bundle', 'title': '本地运行环境', 'order': 30, 'fields': [
            {'key': 'bundle', 'label': 'SakuraTTS 整合包', 'type': 'resource',
             'actionIds': ['downloadBundle', 'installDownload', 'checkUpdate', 'importBundle', 'cancelImport'],
             'default': provider.bundle.load()['bundle']},
            {'key': 'bundlePath', 'label': '本地整合包路径', 'type': 'string', 'default': ''},
        ], 'actions': [
            {'actionId': 'downloadBundle', 'label': '下载并安装'},
            {'actionId': 'installDownload', 'label': '安装已下载整合包'},
            {'actionId': 'checkUpdate', 'label': '检查更新'},
            {'actionId': 'importBundle', 'label': '导入整合包', 'filePicker': {'field': 'bundlePath', 'extensions': ['zip', '7z', 'gz']}},
            {'actionId': 'cancelImport', 'label': '取消'},
        ]}, load=provider.bundle.load, save=lambda values: None,
            actions={'importBundle': provider.bundle.start, 'cancelImport': provider.bundle.cancel,
                     'checkUpdate': provider.bundle.check_update, 'downloadBundle': provider.bundle.download,
                     'installDownload': provider.bundle.install_download})
        surface.register('bundle', 'plugin')
        announcement = UpdateAnnouncement(context.get('sakura.host.chat'), provider.bundle,
                                          lambda: provider.config['autoCheckUpdates'], provider.log)
        context.on('sakura.host.chat.completed.v2', announcement.completed)
        context.effect(announcement.close)
        announcement.start()
        if provider.config['autoCheckUpdates']:
            provider.bundle.check_update()
