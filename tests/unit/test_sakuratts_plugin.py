import io
import json
from pathlib import Path
import shutil
import threading
from types import SimpleNamespace
import zipfile

import pytest

from plugins.optional.sakura_sakuratts._bundle import BundleStore, unpack
from plugins.optional.sakura_sakuratts.plugin import Provider, character_voice
from app.plugin_sdk.sakura_tools import ToolRegistry
from app.core_host.plugin_runtime_application import PluginRuntimeApplication
from app.plugins.inventory import PluginInventory
from app.storage.runtime_roots import RuntimeRoots


def test_voice_reads_studio_resources_without_private_extension_or_manifest_writes(tmp_path):
    from app.core_host.plugin_character import PluginCharacterStore
    package = tmp_path / 'physical-character-directory'
    package.mkdir()
    (package / 'refs.txt').write_text('neutral.wav|JA|neutral|中性\nhappy.wav|JA|happy|开心', encoding='utf-8')
    for name in ('voice.ckpt', 'voice.pth', 'override.ckpt', 'neutral.wav', 'happy.wav'):
        (package / name).write_bytes(b'fixture')
    manifest = package / 'character.json'
    manifest.write_text(json.dumps({'extensions': {'sakura.tts.gpt-sovits': {
        'toneRefs': 'refs.txt', 'gptModel': 'voice.ckpt', 'sovitsModel': 'voice.pth', 'textLang': 'ja'}}}), encoding='utf-8')
    store = PluginCharacterStore(tmp_path)
    store._manifest_paths['logical-id'] = manifest
    character = SimpleNamespace(get=lambda cid: store.get('sakura.tts.sakuratts', cid),
                                resolve_resource=store.resolve_resource)
    original = manifest.read_bytes()
    voice = character_voice(character, 'logical-id', '开心')
    assert voice['gpt'] == str(package / 'voice.ckpt')
    assert voice['sovits'] == str(package / 'voice.pth')
    assert voice['ref_audio_path'] == str(package / 'happy.wav')
    assert voice['prompt_lang'] == 'ja'
    assert voice['prompt_text'] == 'happy'
    assert character_voice(character, 'logical-id', 'unknown')['ref_audio_path'] == str(package / 'neutral.wav')
    assert manifest.read_bytes() == original
    store.update('sakura.tts.sakuratts', 'logical-id', {'gptModel': 'override.ckpt'})
    assert character_voice(character, 'logical-id')['gpt'] == str(package / 'override.ckpt')


def bundle_archive(path, version='one', target='macos-arm64'):
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('runtime/portable.json', json.dumps({'format': 'sakuratts-portable-v1', 'has_preparation': True,
            'release': {'target': target, 'version': version, 'backends': ['mlx'], 'backend': 'mlx',
                        'python_executable': 'runtime/main/bin/python3'}}))
        z.writestr('launcher.py', '')
        z.writestr('runtime/main/bin/python3', '')
    return path


def test_import_keeps_old_environment_when_check_fails_or_cancelled(tmp_path, monkeypatch):
    logs = []
    monkeypatch.setattr('plugins.optional.sakura_sakuratts._bundle.platform.system', lambda: 'Darwin')
    monkeypatch.setattr('plugins.optional.sakura_sakuratts._bundle.platform.machine', lambda: 'arm64')
    (tmp_path / 'installed').mkdir()
    (tmp_path / 'installed/current.json').write_text('{broken')
    store = BundleStore(tmp_path / 'installed', lambda *_: None, lambda action: action(),
                        lambda level, message, **fields: logs.append((level, message, fields)))
    assert store.current() is None
    assert store.load()['bundle']['taskState'] == 'failed'
    assert store.load()['bundle']['detail']
    store.start({'bundlePath': str(bundle_archive(tmp_path / 'one.zip'))})
    store.thread.join(3)
    original = store.current()[0]
    assert store.state == 'succeeded'
    assert store.load()['bundle']['detail'] == ''
    def fail(*_):
        raise ValueError('broken runtime')
    store.probe = fail
    store.start({'bundlePath': str(bundle_archive(tmp_path / 'two.zip', 'two'))})
    store.thread.join(3)
    assert store.state == 'failed' and 'broken runtime' in store.error
    assert store.current()[0] == original
    checking, release = threading.Event(), threading.Event()
    def probe(*_):
        checking.set()
        assert release.wait(3)
    store.probe = probe
    store.start({'bundlePath': str(tmp_path / 'two.zip')})
    assert checking.wait(3)
    store.cancel()
    release.set()
    store.close()
    assert store.state == 'cancelled' and store.current()[0] == original
    assert len(list((store.directory / 'versions').iterdir())) == 1
    assert [fields['event'] for _, _, fields in logs] == [
        'tts.bundle.extracting', 'tts.bundle.checking', 'tts.bundle.succeeded',
        'tts.bundle.extracting', 'tts.bundle.checking', 'tts.bundle.failed',
        'tts.bundle.extracting', 'tts.bundle.checking', 'tts.bundle.cancelled',
    ]
    assert logs[5][0] == 'error'
    assert 'broken runtime' in logs[5][2]['diagnostic']
    assert logs[-1][0] == 'info'


def test_archive_paths_cannot_escape_installation(tmp_path):
    archive = tmp_path / 'bad.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('../outside', 'bad')
    with pytest.raises(ValueError, match='路径'):
        unpack(archive, tmp_path / 'candidate', threading.Event())
    assert not (tmp_path / 'outside').exists()


def provider(tmp_path, monkeypatch):
    released = []
    logger = SimpleNamespace(**{level: lambda *_args, **_kwargs: True for level in ('debug', 'info', 'warning', 'error')})
    services = {'sakura.host.character': object(), 'sakura.tts': SimpleNamespace(status=lambda _: {
        'enabled': True, 'providerId': 'sakura.tts.sakuratts'}),
        'sakura.host.logging': logger,
        'sakura.host.artifacts': SimpleNamespace(allocate=lambda _: {'artifactId': 'audio', 'path': str(tmp_path/'audio.wav')},
            release=released.append, commit=lambda _: {'artifactId': 'audio'})}
    context = SimpleNamespace(get=services.get, data_path=lambda _: str(tmp_path),
                              config=SimpleNamespace(get=lambda: {}))
    result = Provider(context)
    monkeypatch.setattr(result.bundle, 'current', lambda: (tmp_path, {}))
    monkeypatch.setattr('plugins.optional.sakura_sakuratts.plugin.character_voice', lambda *_: {
        'gpt': 'gpt', 'sovits': 'sovits', 'text_lang': 'ja', 'ref_audio_path': 'a.wav', 'prompt_lang': 'ja', 'prompt_text': 'a'})
    return result, released


def test_prewake_only_for_selected_enabled_conversation(tmp_path, monkeypatch):
    p, _ = provider(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(p.runtime, 'start', lambda *_: calls.append('start'))
    monkeypatch.setattr(p.runtime, 'request', lambda *args: calls.append(args))
    try:
        assert p.warmup('character') is False
        assert calls == []
        p.on_chat({'characterId': 'character'})
        p.executor.submit(lambda: None).result(3)
        assert calls == ['start', ('/runtime/wake', {'keep_alive_seconds': 0}),
                         ('/set_refer_audio?refer_audio_path=a.wav',)]
        p.config['prewake'] = False
        p.on_chat({'characterId': 'character'})
        p.executor.submit(lambda: None).result(3)
        assert len(calls) == 3
        p.config['prewake'] = True
        p.hub.status = lambda _: {'enabled': False, 'providerId': 'sakura.tts.sakuratts'}
        p.on_chat({'characterId': 'character'})
        p.executor.submit(lambda: None).result(3)
        assert len(calls) == 3
    finally:
        p.close()


def test_cancel_stops_writer_before_releasing_artifact_without_poll(tmp_path, monkeypatch):
    p, released = provider(tmp_path, monkeypatch)
    writing, stop = threading.Event(), threading.Event()
    monkeypatch.setattr(p.runtime, 'start', lambda *_: None)
    def request(*_):
        writing.set()
        assert stop.wait(3)
        return b'audio'
    monkeypatch.setattr(p.runtime, 'request', request)
    monkeypatch.setattr(p.runtime, 'stop', stop.set)
    try:
        job = p.begin({'characterId': 'character', 'text': 'text'})
        assert writing.wait(3)
        assert released == []
        assert p.cancel(job)
        p.executor.submit(lambda: None).result(3)
        assert released == ['audio']
        assert not (tmp_path / 'audio.wav').exists()
        assert p.poll(job) == {'state': 'cancelled'}
        assert released == ['audio']
    finally:
        p.close()


@pytest.mark.parametrize('installed', ['none', 'missing-interpreter', 'invalid-marker'])
def test_plugin_loads_in_isolated_host_with_native_settings(tmp_path, installed):
    repo = Path(__file__).parents[2]
    shutil.copytree(repo / 'plugins/builtin/sakura_tts_hub', tmp_path / 'plugins/builtin/sakura_tts_hub')
    shutil.copytree(repo / 'plugins/optional/sakura_sakuratts', tmp_path / 'plugins/user/sakura_sakuratts')
    config = tmp_path / 'data/plugins/sakura.tts.sakuratts/config.json'
    config.parent.mkdir(parents=True)
    config.write_text('{"enabled":true,"idleSeconds":90,"prewake":false,"cudaProfile":"fp32"}')
    if installed != 'none':
        import platform
        bundles = config.parent / 'bundles'
        bundle_root = bundles / 'versions/old'
        bundle_root.mkdir(parents=True)
        marker = bundles / 'current.json'
        marker.write_text(json.dumps({'directory': 'versions/old'}))
        if installed == 'invalid-marker':
            marker.write_text('{broken')
        else:
            target = {('Windows', 'amd64'): 'windows-x64', ('Windows', 'x86_64'): 'windows-x64',
                      ('Darwin', 'arm64'): 'macos-arm64'}.get((platform.system(), platform.machine().lower()))
            archive = bundle_archive(tmp_path / 'old.zip', target=target)
            with zipfile.ZipFile(archive) as z:
                z.extractall(bundle_root)
            (bundle_root / 'runtime/main/bin/python3').unlink()
    from app.plugins.inventory import PluginDesiredStateStore
    from app.plugins.dependencies import PluginDependencyRoots
    from app.storage.paths import StoragePaths
    import sys
    PluginDesiredStateStore(tmp_path).set('sakura.tts.sakuratts', True)
    declaration = PluginDependencyRoots(tmp_path).declaration(tmp_path / 'plugins/user/sakura_sakuratts')
    dependency_root = StoragePaths(tmp_path).plugin_dependency_root_for('sakura.tts.sakuratts')
    dependency_root.mkdir(parents=True)
    (dependency_root / '.sakura-dependencies.json').write_text(json.dumps({
        'schemaVersion': 1, 'kind': declaration.kind, 'python': f'{sys.version_info.major}.{sys.version_info.minor}'}))
    roots = RuntimeRoots(tmp_path, tmp_path)
    host = PluginRuntimeApplication(roots, 'sakuratts-test', ToolRegistry(), PluginInventory(roots).scan().runtime_specs)
    try:
        host.start()
        assert host.wait_until_loaded(timeout=5)
        snapshot = host.public_snapshot()
        plugin = next(p for p in snapshot['plugins'] if p['pluginId'] == 'sakura.tts.sakuratts')
        assert plugin['state'] == 'active', plugin
        sections = host.settings_sections('plugin')
        assert [s['sectionId'] for s in sections] == ['overview', 'runtime', 'bundle'], sections
        assert all(s['reasonCode'] == 'READY' for s in sections), sections
        runtime = next(s for s in sections if s['sectionId'] == 'runtime')
        precision = next(f for f in runtime['fields'] if f['key'] == 'cudaProfile')
        assert precision['enabledWhen'] == {'field': 'backend', 'equals': 'cuda', 'hide': True}
        assert precision['default'] == 'fp16'
        assert {o['value'] for o in precision['options']} == {'fp16', 'fp32', 'low-memory', 'minimum-memory'}
        section = next(s for s in sections if s['sectionId'] == 'bundle')
        assert section['actions'][0]['filePicker']['field'] == 'bundlePath'
        if installed != 'none':
            assert section['values']['bundle']['taskState'] == 'failed'
            assert section['values']['bundle']['detail']
            assert section['values']['bundle']['availableActionIds'] == ['importBundle']
            overview = next(s for s in sections if s['sectionId'] == 'overview')
            assert overview['values']['engineState']['state'] == 'error'
        assert host.call_service('sakura.tts.provider.sakuratts', 'status')['available'] is False
        host.settings_save('sakura.tts.sakuratts', 'runtime', {'idleSeconds': 120})
        saved = next(s for s in host.settings_sections('plugin') if s['sectionId'] == 'runtime')['values']
        assert saved['idleSeconds'] == 120
        assert saved['prewake'] is False
        assert saved['cudaProfile'] == 'fp32'
        host.emit_event('sakura.host.chat.request.started', {'characterId': 'fixture'})
    finally:
        host.close()


def test_7z_import_extracts_in_cancellable_child(tmp_path):
    import py7zr
    source = tmp_path / 'entry.txt'
    source.write_text('离线运行环境')
    archive = tmp_path / 'bundle.7z'
    with py7zr.SevenZipFile(archive, 'w') as z:
        z.write(source, 'entry.txt')
    destination = tmp_path / 'installed'
    destination.mkdir()
    unpack(archive, destination, threading.Event())
    assert (destination / 'entry.txt').read_text() == '离线运行环境'


def test_closed_installer_cannot_start_another_import(tmp_path):
    archive = bundle_archive(tmp_path / 'bundle.zip')
    store = BundleStore(tmp_path / 'installed', lambda *_: None, lambda action: action())
    store.close()
    with pytest.raises(RuntimeError, match='停止'):
        store.start({'bundlePath': str(archive)})
    assert store.thread is None


def test_engine_state_distinguishes_on_demand_start_failure_and_sleep(tmp_path, monkeypatch):
    p, _ = provider(tmp_path, monkeypatch)
    try:
        assert p.engine_state()['state'] == 'ready'
        assert p.runtime.process is None
        p.wake = threading.Event()
        assert p.engine_state()['state'] == 'working'
        p.wake = None
        p.record_error(RuntimeError('runtime failed'))
        assert p.engine_state()['state'] == 'error'
        p.publish(lambda: None)
        assert p.engine_state()['state'] == 'ready'
        monkeypatch.setattr(p.runtime, 'status', lambda: '已休眠')
        assert p.engine_state()['state'] == 'ready'
        monkeypatch.setattr(p.bundle, 'current', lambda: None)
        assert p.engine_state()['state'] == 'warning'
    finally:
        p.close()


def test_runtime_busy_is_visible_without_waking_model(tmp_path, monkeypatch):
    from plugins.optional.sakura_sakuratts._runtime import Runtime
    runtime = Runtime(tmp_path)
    runtime.process = SimpleNamespace(poll=lambda: None)
    calls = []
    def request(path, **kwargs):
        calls.append(path)
        return {'state': 'awake', 'busy': True}
    monkeypatch.setattr(runtime, 'request', request)
    assert runtime.status() == '正在合成'
    assert calls == ['/runtime']


def test_precision_reaches_engine_and_switching_profile_restarts_service(tmp_path, monkeypatch):
    from plugins.optional.sakura_sakuratts import _runtime
    from plugins.optional.sakura_sakuratts.plugin import configuration
    launches, stopped = [], []
    def launch(*args, **kwargs):
        process = SimpleNamespace(poll=lambda: None)
        launches.append(process)
        return process
    monkeypatch.setattr(_runtime.subprocess, 'Popen', launch)
    monkeypatch.setattr(_runtime, 'terminate_process_tree', lambda process, **kw: stopped.append(process))
    runtime = _runtime.Runtime(tmp_path / 'data')
    monkeypatch.setattr(runtime, 'request', lambda *args, **kwargs: {})
    bundle = tmp_path, {'backend': 'cpu', 'backends': ['cuda', 'cpu', 'directml', 'mlx']}
    monkeypatch.setattr(_runtime, 'probe', lambda *args: None)
    voice, cancel = {'gpt': 'voice.ckpt', 'sovits': 'voice.pth'}, threading.Event()
    try:
        for index, profile in enumerate(('fp16', 'fp32', 'low-memory', 'minimum-memory'), 1):
            config = configuration({'backend': 'cuda', 'cudaProfile': profile})
            runtime.start(bundle, config, voice, cancel)
            runtime.start(bundle, config, voice, cancel)
            assert len(launches) == index
            settings = json.loads((runtime.directory / 'inference.json').read_text(encoding='utf-8'))
            assert settings['sakuratts'] == {'backend': 'cuda', 'profile': profile}
            assert settings['custom']['is_half'] is False
            assert len(stopped) == index - 1
        for backend in ('cpu', 'directml', 'mlx', 'auto'):
            runtime.start(bundle, configuration({'backend': backend, 'cudaProfile': 'minimum-memory'}), voice, cancel)
            settings = json.loads((runtime.directory / 'inference.json').read_text(encoding='utf-8'))
            assert 'profile' not in settings['sakuratts']
        assert configuration({'backend': 'cuda'})['cudaProfile'] == 'fp16'
        with pytest.raises(ValueError, match='档位'):
            configuration({'cudaProfile': 'invalid'})
    finally:
        runtime.stop()


@pytest.mark.parametrize('failure', [RuntimeError('CUDA unavailable'), TimeoutError('check timed out'),
                                    OSError('cannot execute check')])
def test_auto_backend_rechecks_on_restart_and_preserves_check_errors(tmp_path, monkeypatch, failure):
    from plugins.optional.sakura_sakuratts import _runtime
    from plugins.optional.sakura_sakuratts.plugin import configuration
    checks, launches = [], []
    def probe(*args):
        checks.append('cuda')
        if len(checks) == 1:
            raise failure
    def launch(*args, **kwargs):
        process = SimpleNamespace(poll=lambda: None)
        launches.append(process)
        return process
    monkeypatch.setattr(_runtime, 'probe', probe)
    monkeypatch.setattr(_runtime.subprocess, 'Popen', launch)
    monkeypatch.setattr(_runtime, 'terminate_process_tree', lambda *args, **kwargs: None)
    runtime = _runtime.Runtime(tmp_path)
    monkeypatch.setattr(runtime, 'request', lambda *args, **kwargs: {})
    bundle = tmp_path, {'backend': 'cpu', 'backends': ['cpu', 'cuda']}
    voice, cancel = {'gpt': 'voice.ckpt', 'sovits': 'voice.pth'}, threading.Event()
    config = configuration({})
    try:
        if isinstance(failure, RuntimeError):
            runtime.start(bundle, config, voice, cancel)
            assert runtime.backend == 'cpu'
            assert (tmp_path / 'backend-check.log').read_text() == str(failure)
            runtime.start(bundle, config, voice, cancel)
            assert len(checks) == len(launches) == 1
            runtime.stop()
        else:
            with pytest.raises(type(failure), match=str(failure)):
                runtime.start(bundle, config, voice, cancel)
            assert launches == []
        runtime.start(bundle, config, voice, cancel)
        assert runtime.backend == 'cuda'
        assert runtime.selection_reason == ''
        runtime.start(bundle, config, voice, cancel)
        assert len(checks) == 2
        assert len(launches) == (2 if isinstance(failure, RuntimeError) else 1)
    finally:
        runtime.stop()


def test_runtime_preserves_engine_http_error_details(tmp_path, monkeypatch):
    from urllib.error import HTTPError
    from plugins.optional.sakura_sakuratts._runtime import Runtime
    response = io.BytesIO(json.dumps({'message': 'tts failed', 'Exception': 'DLL load failed: path too long'}).encode())
    def fail(*args, **kwargs):
        raise HTTPError('http://127.0.0.1/tts', 400, 'Bad Request', {}, response)
    monkeypatch.setattr('plugins.optional.sakura_sakuratts._runtime.urlopen_direct_for_loopback', fail)
    runtime = Runtime(tmp_path)
    runtime.url = 'http://127.0.0.1'
    with pytest.raises(RuntimeError, match='DLL load failed: path too long'):
        runtime.request('/tts', {'text': 'test'})
    assert response.closed


def test_synthesis_metrics_use_host_tts_log_without_text_or_prompt(tmp_path, monkeypatch):
    from plugins.optional.sakura_sakuratts._runtime import Runtime
    from app.core_host.plugin_host_services import _LoggingHostService
    from app.plugins.host_services import HOST_CALLER, HOST_CALLER_LOG_METADATA
    records = []
    monkeypatch.setattr('app.core_host.plugin_host_services.log_message',
        lambda severity, message, **fields: records.append((severity, message, fields)))
    def emit(level, message, **fields):
        caller = HOST_CALLER.set('sakura.tts.sakuratts')
        metadata = HOST_CALLER_LOG_METADATA.set(('SakuraTTS', ('sakura.tts.provider.sakuratts',)))
        try:
            _LoggingHostService().call('emit', [[{'severity': level, 'message': message, 'fields': fields}], 0])
        finally:
            HOST_CALLER.reset(caller)
            HOST_CALLER_LOG_METADATA.reset(metadata)
    response = io.BytesIO(b'wav-fixture')
    response.headers = {'X-SakuraTTS-Reference-Ms': '1400', 'X-SakuraTTS-Semantic-Ms': '700',
        'X-SakuraTTS-Acoustic-Ms': '400', 'X-SakuraTTS-Audio-Seconds': '11.5',
        'X-SakuraTTS-Reference-Cache': 'miss', 'X-SakuraTTS-Frontend-Ms': 'nan'}
    monkeypatch.setattr('plugins.optional.sakura_sakuratts._runtime.urlopen_direct_for_loopback', lambda *_args, **_kw: response)
    runtime = Runtime(tmp_path, emit)
    runtime.url, runtime.backend = 'http://127.0.0.1', 'cuda'
    assert runtime.request('/tts', {'text': 'private body', 'prompt_text': 'private reference'}) == b'wav-fixture'
    record = records[-1][2]
    assert record['component'] == 'tts'
    assert record['plugin_id'] == 'sakura.tts.sakuratts'
    assert record['fields']['reference_ms'] == 1400
    assert record['fields']['gpt_ms'] == 700
    assert record['fields']['sovits_ms'] == 400
    assert record['fields']['reference_cache'] == 'miss'
    assert 'frontend_ms' not in record['fields']
    assert 'private' not in json.dumps(records)
