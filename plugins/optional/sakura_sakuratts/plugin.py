from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
import uuid

try:
    from ._bundle import BundleStore, Cancelled
    from ._runtime import Runtime, probe
except ImportError:
    from _bundle import BundleStore, Cancelled
    from _runtime import Runtime, probe

PROVIDER_ID = 'sakura.tts.sakuratts'
SERVICE_KEY = 'sakura.tts.provider.sakuratts'
DEFAULTS = {'backend': 'auto', 'idleSeconds': 60, 'prewake': True}


def configuration(values):
    result = {key: values.get(key, value) for key, value in DEFAULTS.items()}
    if result['backend'] not in {'auto', 'cpu', 'cuda', 'directml', 'mlx'}:
        raise ValueError('不支持此推理后端。')
    if type(result['idleSeconds']) is not int or result['idleSeconds'] < 1:
        raise ValueError('空闲休眠时间必须是正整数。')
    if type(result['prewake']) is not bool:
        raise ValueError('提前唤醒设置无效。')
    return result


def character_voice(character, character_id, tone='中性'):
    extension = character.get(character_id)
    resolve = lambda key: str(character.resolve_resource(character_id, extension[key]))
    references = []
    for line in Path(resolve('toneRefs')).read_text(encoding='utf-8-sig').splitlines():
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
    return {'gpt': resolve('gptModel'), 'sovits': resolve('sovitsModel'),
            'ref_audio_path': str(character.resolve_resource(character_id, reference[0])),
            'prompt_lang': reference[1].lower(), 'prompt_text': reference[2],
            'text_lang': extension.get('textLang', 'ja')}


class Provider:
    def __init__(self, context):
        self.context = context
        self.character = context.get('sakura.host.character')
        self.artifacts = context.get('sakura.host.artifacts')
        self.hub = context.get('sakura.tts')
        self.directory = Path(context.data_path('.'))
        self.runtime = Runtime(self.directory / 'runtime-data')
        self.config = configuration(context.config.get())
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='sakuratts')
        self.lock = threading.RLock()
        self.jobs = {}
        self.closed = False
        self.active = None
        self.wake = None
        self.error = ''
        self.bundle = BundleStore(self.directory / 'bundles', probe, self.publish)

    def publish(self, activate):
        def switch():
            self.runtime.stop()
            activate()
        self.executor.submit(switch).result()

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
            if self.closed or not self.config['prewake'] or not selected['enabled'] or selected['providerId'] != PROVIDER_ID:
                return
            if self.wake is not None or not self.bundle.current():
                return
            cancel = threading.Event()
            self.wake = cancel
            self.executor.submit(self._prewake, character_id, cancel)

    def _prewake(self, character_id, cancel):
        try:
            voice = character_voice(self.character, character_id)
            self.runtime.start(self.bundle.current(), self.config, voice, cancel)
            if not cancel.is_set():
                self.runtime.request('/runtime/wake', {'keep_alive_seconds': 0})
        except Cancelled:
            pass
        except Exception as error:
            self.record_error(error)
        finally:
            with self.lock:
                if self.wake is cancel:
                    self.wake = None

    def record_error(self, error):
        self.error = str(error)
        (self.directory / 'last-error.log').write_text(self.error, encoding='utf-8')

    def begin(self, request):
        with self.lock:
            if self.closed or not self.bundle.current():
                return {'errorCode': 'TTS_RUNTIME_NOT_INSTALLED'}
            try:
                voice = character_voice(self.character, request['characterId'], request.get('options', {}).get('tone', '中性'))
            except Exception as error:
                self.record_error(error)
                return {'errorCode': 'TTS_CHARACTER_CONFIG_INVALID'}
            allocation = self.artifacts.allocate({'mediaType': 'audio/wav', 'suffix': '.wav'})
            job_id = 'job_' + uuid.uuid4().hex
            job = {'state': 'running', 'cancel': threading.Event(), 'allocation': allocation, 'released': False}
            self.jobs[job_id] = job
            self.executor.submit(self._synthesize, job, request, voice)
            return job_id

    def _release(self, job):
        if not job['released']:
            self.artifacts.release(job['allocation']['artifactId'])
            job['released'] = True

    def _synthesize(self, job, request, voice):
        try:
            with self.lock:
                if job['cancel'].is_set():
                    raise Cancelled()
                self.active = job
            self.runtime.start(self.bundle.current(), self.config, voice, job['cancel'])
            payload = {key: voice[key] for key in ('text_lang', 'ref_audio_path', 'prompt_lang', 'prompt_text')}
            payload.update(text=request['text'], parallel_infer=False, streaming_mode=False, media_type='wav')
            audio = self.runtime.request('/tts', payload)
            with self.lock:
                if job['cancel'].is_set():
                    raise Cancelled()
                Path(job['allocation']['path']).write_bytes(audio)
                job['state'] = 'succeeded'
        except Cancelled:
            with self.lock:
                job['state'] = 'cancelled'
        except Exception as error:
            self.record_error(error)
            with self.lock:
                job['state'] = 'cancelled' if job['cancel'].is_set() else 'failed'
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
                        return {'state': 'failed', 'errorCode': 'TTS_ARTIFACT_INVALID'}
                return {'state': state, **({'errorCode': 'TTS_SYNTHESIS_FAILED'} if state == 'failed' else {})}
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
            if config == self.config:
                return 'applied'
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
        settings.register({'sectionId': 'bundle', 'title': '运行环境', 'fields': [
            {'key': 'bundle', 'label': 'SakuraTTS 整合包', 'type': 'resource',
             'actionIds': ['importBundle', 'cancelImport'], 'default': provider.bundle.load()['bundle']},
            {'key': 'bundlePath', 'label': '本地整合包路径', 'type': 'string', 'default': '', 'placement': 'advanced'},
        ], 'actions': [
            {'actionId': 'importBundle', 'label': '导入整合包', 'filePicker': {'field': 'bundlePath', 'extensions': ['zip', '7z', 'gz']}},
            {'actionId': 'cancelImport', 'label': '取消导入'},
        ]}, load=provider.bundle.load, save=lambda values: None,
            actions={'importBundle': provider.bundle.start, 'cancelImport': provider.bundle.cancel})
        surface.register('bundle', 'plugin')
        import platform
        options = [('auto', '自动'), ('mlx', 'Apple GPU（MLX）')] if platform.system() == 'Darwin' else [
            ('auto', '自动'), ('cpu', 'CPU'), ('cuda', 'NVIDIA CUDA'), ('directml', 'DirectML')]
        settings.register({'sectionId': 'runtime', 'title': '推理与资源', 'fields': [
            {'key': 'engineState', 'label': '引擎状态', 'type': 'readonly', 'default': '未启动'},
            {'key': 'backend', 'label': '推理后端', 'type': 'select', 'default': 'auto',
             'options': [{'value': value, 'label': label} for value, label in options]},
            {'key': 'idleSeconds', 'label': '空闲休眠（秒）', 'type': 'integer', 'minimum': 1, 'default': 60},
            {'key': 'prewake', 'label': '提前唤醒', 'type': 'boolean', 'default': True,
             'description': '利用等待大模型 API 返回的时间，提前加载语音模型。'},
        ]}, load=lambda: {**provider.config, 'engineState': provider.runtime.status() +
            (f' · {provider.runtime.backend}' if provider.runtime.backend else '') +
            (f' · {provider.runtime.selection_reason}' if provider.runtime.selection_reason else '')},
            save=lambda values: context.config.update(configuration(values)))
        surface.register('runtime', 'plugin')
