import io
import json
from pathlib import Path
import shutil
import threading
from types import SimpleNamespace
import zipfile

import pytest

from plugins.builtin.sakura_sakuratts._bundle import BundleStore, unpack
from plugins.builtin.sakura_sakuratts.plugin import Provider, character_voice
from app.plugin_sdk.sakura_tools import ToolRegistry
from app.core_host.plugin_runtime_application import PluginRuntimeApplication
from app.plugins.inventory import PluginInventory
from app.storage.runtime_roots import RuntimeRoots


@pytest.fixture(autouse=True)
def isolated_device_inventory(monkeypatch):
    monkeypatch.setattr('plugins.builtin.sakura_sakuratts._runtime.nvidia_device', lambda: {})


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


def bundle_archive(path, version='one', target='macos-arm64', source_commit='fixture'):
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('runtime/portable.json', json.dumps({'format': 'sakuratts-portable-v1', 'has_preparation': True,
            'release': {'target': target, 'version': version, 'backends': ['mlx'], 'backend': 'mlx',
                        'source_commit': source_commit, 'source_dirty': False,
                        'python_executable': 'runtime/main/bin/python3'}}))
        z.writestr('launcher.py', '')
        z.writestr('runtime/main/bin/python3', '')
    return path


def test_import_keeps_old_environment_when_check_fails_or_cancelled(tmp_path, monkeypatch):
    logs = []
    monkeypatch.setattr('plugins.builtin.sakura_sakuratts._bundle.platform.system', lambda: 'Darwin')
    monkeypatch.setattr('plugins.builtin.sakura_sakuratts._bundle.platform.machine', lambda: 'arm64')
    (tmp_path / 'installed').mkdir()
    (tmp_path / 'installed/current.json').write_text('{broken')
    store = BundleStore(tmp_path / 'installed', lambda *_: None, lambda action: action(),
                        lambda level, message, **fields: logs.append((level, message, fields)))
    assert store.current() is None
    assert store.load()['bundle']['taskState'] == 'failed'
    assert store.load()['bundle']['detail']
    assert logs[0][0] == 'error'
    assert logs[0][2]['stage'] == 'installed_bundle_read'
    logs.clear()
    store.start({'bundlePath': str(bundle_archive(tmp_path / 'one.zip'))})
    store.thread.join(3)
    original = store.current()[0]
    assert store.state == 'succeeded'
    assert store.load()['bundle']['detail'] == ''
    def fail(*_):
        raise ValueError('broken runtime')
    store.probe = fail
    (store.directory / 'import-error.log').mkdir()
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
    assert store.state == 'idle' and store.current()[0] == original
    assert store.load()['bundle']['message'] == ''
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
    monkeypatch.setattr('plugins.builtin.sakura_sakuratts.plugin.character_voice', lambda *_: {
        'gpt': 'gpt', 'sovits': 'sovits', 'text_lang': 'ja', 'ref_audio_path': 'a.wav', 'prompt_lang': 'ja', 'prompt_text': 'a'})
    return result, released


@pytest.mark.parametrize('idle_seconds', [0, 60])
def test_prewake_only_for_selected_enabled_conversation(tmp_path, monkeypatch, idle_seconds):
    p, _ = provider(tmp_path, monkeypatch)
    p.reconfigure({**p.config, 'idleSeconds': idle_seconds})
    calls = []
    monkeypatch.setattr(p.runtime, 'start', lambda *_: calls.append('start'))
    monkeypatch.setattr(p.runtime, 'request', lambda *args: calls.append(args))
    try:
        assert p.warmup('character') is False
        assert calls == []
        p.on_chat({'characterId': 'character'})
        p.executor.submit(lambda: None).result(3)
        expected = ['start']
        if idle_seconds:
            expected.append(('/runtime/wake', {'keep_alive_seconds': 0}))
        expected.append(('/set_refer_audio?refer_audio_path=a.wav',))
        assert calls == expected
        p.hub.status = lambda _: {'enabled': False, 'providerId': 'sakura.tts.sakuratts'}
        p.on_chat({'characterId': 'character'})
        p.executor.submit(lambda: None).result(3)
        assert calls == expected
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
    shutil.copytree(repo / 'plugins/builtin/sakura_sakuratts', tmp_path / 'plugins/builtin/sakura_sakuratts')
    config = tmp_path / 'data/plugins/sakura.tts.sakuratts/config.json'
    config.parent.mkdir(parents=True)
    config.write_text('{"enabled":true,"idleSeconds":90,"cudaProfile":"fp32","autoCheckUpdates":false}')
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
    from app.plugins.dependencies import PluginDependencyRoots
    import sys
    declaration = PluginDependencyRoots(tmp_path).declaration(tmp_path / 'plugins/builtin/sakura_sakuratts')
    dependency_root = tmp_path / 'plugins/dependencies/sakura.tts.sakuratts'
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
        status = host.call_service('sakura.tts', 'status', 'fixture')
        assert any(p['providerId'] == 'sakura.tts.sakuratts' for p in status['providers'])
        assert status['enabled'] is False
        sections = host.settings_sections('plugin')
        assert [s['sectionId'] for s in sections] == ['overview', 'runtime', 'bundle'], sections
        assert all(s['reasonCode'] == 'READY' for s in sections), sections
        runtime = next(s for s in sections if s['sectionId'] == 'runtime')
        precision = next(f for f in runtime['fields'] if f['key'] == 'cudaProfile')
        assert precision['enabledWhen'] == {'field': 'backend', 'equals': 'cuda', 'hide': True}
        assert precision['default'] == 'fp16'
        assert {o['value'] for o in precision['options']} == {'fp16', 'fp32', 'low-memory', 'minimum-memory'}
        section = next(s for s in sections if s['sectionId'] == 'bundle')
        assert section['actions'][0]['actionId'] == 'downloadBundle'
        import_action = next(action for action in section['actions'] if action['actionId'] == 'importBundle')
        assert import_action['filePicker']['field'] == 'bundlePath'
        if installed != 'none':
            assert section['values']['bundle']['taskState'] == 'failed'
            assert section['values']['bundle']['detail']
            assert section['values']['bundle']['availableActionIds'] == ['downloadBundle', 'checkUpdate', 'importBundle']
            overview = next(s for s in sections if s['sectionId'] == 'overview')
            assert overview['values']['engineState']['state'] == 'error'
        assert host.call_service('sakura.tts.provider.sakuratts', 'status')['available'] is False
        host.settings_save('sakura.tts.sakuratts', 'runtime', {'idleSeconds': 0})
        saved = next(s for s in host.settings_sections('plugin') if s['sectionId'] == 'runtime')['values']
        assert saved['idleSeconds'] == 0
        assert saved['cudaProfile'] == 'fp32'
        host.emit_event('sakura.host.chat.request.started', {'characterId': 'fixture'})
    finally:
        host.close()


@pytest.mark.parametrize('native', [True, False])
def test_7z_import_extracts_in_cancellable_child(tmp_path, monkeypatch, native):
    import py7zr
    from plugins.builtin.sakura_sakuratts import _bundle
    if native:
        if _bundle.seven_zip_executable() is None:
            pytest.skip('Native 7-Zip unavailable')
    else:
        monkeypatch.setattr(_bundle, 'seven_zip_executable', lambda: None)
    source = tmp_path / 'entry.txt'
    source.write_text('离线运行环境')
    archive = tmp_path / 'bundle.7z'
    with py7zr.SevenZipFile(archive, 'w') as z:
        z.write(source, 'entry.txt')
    destination = tmp_path / 'installed'
    destination.mkdir()
    unpack(archive, destination, threading.Event())
    assert (destination / 'entry.txt').read_text() == '离线运行环境'


@pytest.mark.parametrize('action', ['cancel', 'fail'])
def test_7z_child_cancel_and_failure(tmp_path, monkeypatch, action):
    import py7zr
    import subprocess
    import sys
    from plugins.builtin.sakura_sakuratts import _bundle
    archive = tmp_path / 'bundle.7z'
    with py7zr.SevenZipFile(archive, 'w') as z:
        z.writestr('content', 'entry.txt')
    destination = tmp_path / 'installed'
    destination.mkdir()
    cancel = threading.Event()
    children = []
    popen = subprocess.Popen
    def start(_command, **kwargs):
        code = 'import time; time.sleep(60)' if action == 'cancel' else 'import sys; sys.exit("extract failed")'
        child = popen([sys.executable, '-c', code], **kwargs)
        children.append(child)
        if action == 'cancel':
            cancel.set()
        return child
    monkeypatch.setattr(_bundle, 'subprocess', SimpleNamespace(
        **{**vars(subprocess), 'Popen': start}))
    with pytest.raises(_bundle.Cancelled if action == 'cancel' else ValueError,
                       match=None if action == 'cancel' else 'extract failed'):
        unpack(archive, destination, cancel)
    assert len(children) == 1
    assert children[0].poll() is not None


def test_7z_unsafe_member_rejected_before_child_starts(tmp_path, monkeypatch):
    import py7zr
    from plugins.builtin.sakura_sakuratts import _bundle
    archive = tmp_path / 'bad.7z'
    with monkeypatch.context() as writer:
        writer.setattr('py7zr.py7zr.check_archive_path', lambda _: True)
        with py7zr.SevenZipFile(archive, 'w') as z:
            z.writestr('content', '../outside.txt')
    monkeypatch.setattr(_bundle.subprocess, 'Popen', lambda *a, **kw: pytest.fail('Unsafe extraction started'))
    with pytest.raises(ValueError, match='路径'):
        unpack(archive, tmp_path / 'installed', threading.Event())
    assert not (tmp_path / 'outside.txt').exists()


def test_closed_installer_cannot_start_another_import(tmp_path):
    archive = bundle_archive(tmp_path / 'bundle.zip')
    store = BundleStore(tmp_path / 'installed', lambda *_: None, lambda action: action())
    store.close()
    with pytest.raises(RuntimeError, match='停止'):
        store.start({'bundlePath': str(archive)})
    assert store.thread is None


def test_engine_state_distinguishes_on_demand_start_and_failure(tmp_path, monkeypatch):
    p, _ = provider(tmp_path, monkeypatch)
    logs = []
    monkeypatch.setattr(p, 'log', lambda level, message, **fields: logs.append(fields))
    try:
        assert p.engine_state()['state'] == 'ready'
        assert p.runtime.process is None
        p.wake = threading.Event()
        assert p.engine_state()['state'] == 'working'
        p.wake = None
        (tmp_path / 'last-error.log').mkdir()
        p.record_error(RuntimeError('runtime failed'))
        assert p.engine_state()['state'] == 'error'
        assert logs[-1]['diagnostic'] == 'runtime failed'
        p.publish(lambda: None)
        assert p.engine_state()['state'] == 'ready'
        monkeypatch.setattr(p.bundle, 'current', lambda: None)
        assert p.engine_state()['state'] == 'warning'
    finally:
        p.close()


@pytest.mark.parametrize(('runtime_state', 'state', 'label'), [
    ({'state': 'awake', 'busy': True}, 'working', '正在合成'),
    ({'state': 'awake'}, 'ready', '已加载'),
    ({'state': 'sleeping'}, 'ready', '已休眠'),
    ({'state': 'failed'}, 'error', '启动失败'),
])
def test_engine_state_reports_runtime_without_waking_model(tmp_path, monkeypatch, runtime_state, state, label):
    p, _ = provider(tmp_path, monkeypatch)
    p.runtime.process = SimpleNamespace(poll=lambda: None)
    monkeypatch.setattr(p.runtime, 'stop', lambda: None)
    calls = []
    def request(path, **kwargs):
        calls.append(path)
        return runtime_state
    monkeypatch.setattr(p.runtime, 'request', request)
    try:
        result = p.engine_state()
        assert (result['state'], result['label']) == (state, label)
        assert calls == ['/runtime']
    finally:
        p.close()


def test_precision_reaches_engine_and_switching_profile_restarts_service(tmp_path, monkeypatch):
    from plugins.builtin.sakura_sakuratts import _runtime
    from plugins.builtin.sakura_sakuratts.plugin import configuration
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
    from plugins.builtin.sakura_sakuratts import _runtime
    from plugins.builtin.sakura_sakuratts.plugin import configuration
    checks, launches, logs = [], [], []
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
    runtime = _runtime.Runtime(tmp_path, lambda level, message, **fields: logs.append(fields))
    (tmp_path / 'backend-check.log').mkdir()
    monkeypatch.setattr(runtime, 'request', lambda *args, **kwargs: {})
    bundle = tmp_path, {'backend': 'cpu', 'backends': ['cpu', 'cuda']}
    voice, cancel = {'gpt': 'voice.ckpt', 'sovits': 'voice.pth'}, threading.Event()
    config = configuration({})
    try:
        if isinstance(failure, RuntimeError):
            runtime.start(bundle, config, voice, cancel)
            assert runtime.backend == 'cpu'
            assert logs[0]['diagnostic'] == str(failure)
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
    from plugins.builtin.sakura_sakuratts._runtime import Runtime
    response = io.BytesIO(json.dumps({'message': 'tts failed', 'Exception': 'DLL load failed: path too long'}).encode())
    def fail(*args, **kwargs):
        raise HTTPError('http://127.0.0.1/tts', 400, 'Bad Request', {}, response)
    monkeypatch.setattr('plugins.builtin.sakura_sakuratts._runtime.urlopen_direct_for_loopback', fail)
    runtime = Runtime(tmp_path)
    runtime.url = 'http://127.0.0.1'
    with pytest.raises(RuntimeError, match='DLL load failed: path too long'):
        runtime.request('/tts', {'text': 'test'})
    assert response.closed


def test_synthesis_metrics_use_host_tts_log_without_text_or_prompt(tmp_path, monkeypatch):
    from plugins.builtin.sakura_sakuratts._runtime import Runtime
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
    monkeypatch.setattr('plugins.builtin.sakura_sakuratts._runtime.urlopen_direct_for_loopback', lambda *_args, **_kw: response)
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


@pytest.fixture
def online_repo(tmp_path, monkeypatch):
    from functools import partial
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
    from plugins.builtin.sakura_sakuratts import _bundle
    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *_):
            pass
    root = tmp_path / 'remote'
    root.mkdir()
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Handler, directory=str(root)))
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    monkeypatch.setattr(_bundle, 'REPOSITORY_URL', f'http://127.0.0.1:{server.server_port}/')
    monkeypatch.setattr(_bundle.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(_bundle.platform, 'machine', lambda: 'arm64')
    def publish(version, commit):
        folder = root / 'previews' / version
        folder.mkdir(parents=True)
        archive = bundle_archive(folder / 'bundle.zip', source_commit=commit)
        package = {'platform': 'macos-arm64', 'releaseId': version, 'sourceCommit': commit,
                   'bytes': archive.stat().st_size, 'unpackedBytes': 1000,
                   'path': archive.relative_to(root).as_posix(), 'url': 'https://untrusted.invalid/package'}
        (root / 'latest-preview.json').write_text(json.dumps({
            'channel': 'preview', 'releaseId': version, 'sourceCommit': commit, 'packages': [package]}))
        return archive, package
    try:
        yield root, publish
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def finish_bundle_task(store):
    store.thread.join(5)
    assert not store.thread.is_alive()


def test_online_download_install_and_new_published_index(tmp_path, online_repo):
    _, publish = online_repo
    publish('preview-1', 'first')
    probes = []
    store = BundleStore(tmp_path / 'installed', lambda *args: probes.append(args), lambda action: action())
    assert store.load()['bundle']['availableActionIds'][0] == 'downloadBundle'
    store.download()
    finish_bundle_task(store)
    assert store.state == 'succeeded', store.error
    assert store.current()[1]['source_commit'] == 'first'
    assert len(probes) == 1 and store.downloaded is None
    store.close()
    store = BundleStore(store.directory, lambda *_: None, lambda action: action())
    store.check_update()
    finish_bundle_task(store)
    assert 'downloadBundle' not in store.load()['bundle']['availableActionIds']
    publish('preview-2', 'second')
    store.check_update()
    finish_bundle_task(store)
    assert store.available['releaseId'] == 'preview-2'
    assert 'downloadBundle' in store.load()['bundle']['availableActionIds']
    old = store.current()
    store.download()
    finish_bundle_task(store)
    assert store.current()[1]['source_commit'] == 'second'
    assert old[0].is_dir()
    store.close()


@pytest.mark.parametrize('failure', ['truncated', 'wrong-commit', 'http-error'])
def test_online_failure_preserves_installed_bundle(tmp_path, online_repo, failure):
    root, publish = online_repo
    archive, package = publish('preview-2', 'second')
    store = BundleStore(tmp_path / 'installed', lambda *_: None, lambda action: action())
    store.start({'bundlePath': str(bundle_archive(tmp_path / 'old.zip'))})
    finish_bundle_task(store)
    old = store.current()
    store.check_update()
    finish_bundle_task(store)
    if failure == 'truncated':
        archive.write_bytes(b'incomplete')
    elif failure == 'http-error':
        archive.unlink()
    else:
        store.available['sourceCommit'] = 'other'
    store.download()
    finish_bundle_task(store)
    if failure != 'wrong-commit':
        assert store.downloaded is None
        assert not list((store.directory / 'downloads').glob('*.zip'))
    assert store.state == 'failed' and store.error
    assert store.current() == old
    assert len(list((store.directory / 'versions').iterdir())) == 1
    store.close()


def test_cancel_download_waits_for_writer_and_keeps_old_bundle(tmp_path, online_repo, monkeypatch):
    from plugins.builtin.sakura_sakuratts import _bundle
    _, publish = online_repo
    publish('preview-1', 'new')
    store = BundleStore(tmp_path / 'installed', lambda *_: None, lambda action: action())
    store.check_update()
    finish_bundle_task(store)
    reading, resume = threading.Event(), threading.Event()
    class Download(io.BytesIO):
        reads = 0
        def read(self, _):
            self.reads += 1
            if self.reads == 2:
                reading.set()
                assert resume.wait(3)
            return b'chunk'
    monkeypatch.setattr(_bundle, 'urlopen', lambda *args, **kwargs: Download())
    store.download()
    assert reading.wait(3)
    from app.core_host.plugin_host_services import _settings_resource_value_valid
    assert _settings_resource_value_valid({'actionIds': ['cancelImport']}, store.load()['bundle'])
    with pytest.raises(ValueError, match='正在处理'):
        store.check_update()
    store.cancel()
    resume.set()
    store.close()
    assert store.state == 'idle'
    assert store.load()['bundle']['message'] == ''
    assert store.load()['bundle']['availableActionIds'][0] == 'downloadBundle'
    assert store.downloaded is None
    assert not list((store.directory / 'downloads').iterdir())


def test_update_notice_uses_current_character_and_records_only_its_completion(tmp_path):
    from plugins.builtin.sakura_sakuratts._updates import UpdateAnnouncement
    now = [0]
    facts = {'sessionId': 'session', 'idle': False, 'activityRevision': 0, 'interactionRevision': 0}
    submitted = []
    store = BundleStore(tmp_path / 'installed', lambda *_: None, lambda action: action())
    store._current = tmp_path, {'source_commit': 'old'}
    store.available = {'releaseId': 'preview-2', 'sourceCommit': 'new', 'platform': 'macos-arm64'}
    chat = SimpleNamespace(current=lambda: facts, cancel=lambda _: None,
        submit=lambda request: submitted.append(request) or {'accepted': True, 'operationId': 'notice'})
    notice = UpdateAnnouncement(chat, store, lambda: True, lambda *_args, **_kwargs: None, clock=lambda: now[0])
    notice.tick()
    now[0] = 10
    facts['idle'] = True
    notice.tick()
    now[0] = 13
    notice.tick()
    assert len(submitted) == 1
    assert submitted[0]['sessionId'] == 'session' and submitted[0]['resources'] == []
    assert submitted[0]['notification']['kind'] == 'update'
    assert 'SakuraTTS' in submitted[0]['notification']['text']
    assert 'SakuraTTS' in submitted[0]['message']
    assert store.available['releaseId'] not in submitted[0]['message']
    assert store.available['platform'] not in submitted[0]['message']
    assert not notice.marker.exists()
    notice.completed({'operationId': 'unrelated'})
    assert not notice.marker.exists()
    notice.completed({'operationId': 'notice'})
    assert json.loads(notice.marker.read_text())['releaseId'] == 'preview-2'
    notice.close()
    restarted = UpdateAnnouncement(chat, store, lambda: True, lambda *_args, **_kwargs: None)
    restarted.tick()
    assert len(submitted) == 1
    restarted.close()
    store.close()


@pytest.mark.parametrize('mutation', ['path', 'release', 'size', 'platform'])
def test_online_index_rejects_invalid_package_before_download(online_repo, mutation):
    from plugins.builtin.sakura_sakuratts._bundle import online_package
    root, publish = online_repo
    publish('preview-1', 'first')
    index_path = root / 'latest-preview.json'
    index = json.loads(index_path.read_text())
    package = index['packages'][0]
    if mutation == 'path':
        package['path'] = '../outside.zip'
    elif mutation == 'release':
        package['releaseId'] = 'different'
    elif mutation == 'size':
        package['bytes'] = -1
    else:
        package['platform'] = 'windows-x64'
    index_path.write_text(json.dumps(index))
    with pytest.raises(ValueError):
        online_package()


@pytest.mark.parametrize(('initial', 'changes', 'interrupted'), [
    ({}, {'autoCheckUpdates': False}, False),
    ({}, {'cudaProfile': 'fp32'}, False),
    ({}, {'backend': 'cpu'}, True),
    ({}, {'idleSeconds': 30}, True),
    ({'backend': 'cuda'}, {'cudaProfile': 'fp32'}, True),
])
def test_configuration_restarts_only_for_runtime_options(tmp_path, monkeypatch, initial, changes, interrupted):
    p, _ = provider(tmp_path, monkeypatch)
    p.reconfigure({**p.config, **initial})
    stopped = []
    started, finish = threading.Event(), threading.Event()
    monkeypatch.setattr(p.runtime, 'start', lambda *_: None)
    monkeypatch.setattr(p.runtime, 'stop', lambda: stopped.append(True))
    def synthesize(*_):
        started.set()
        assert finish.wait(3)
        return b'audio'
    monkeypatch.setattr(p.runtime, 'request', synthesize)
    try:
        job = p.begin({'characterId': 'character', 'text': 'test'})
        assert started.wait(3)
        assert p.reconfigure({**p.config, **changes}) == 'applied'
        assert bool(stopped) is interrupted
        finish.set()
        p.executor.submit(lambda: None).result(timeout=3)
        assert p.poll(job)['state'] == ('cancelled' if interrupted else 'succeeded')
        assert all(p.config[key] == value for key, value in changes.items())
    finally:
        finish.set()
        p.close()


def test_update_notice_waits_after_activity_and_does_not_mark_failed_reply(tmp_path):
    from plugins.builtin.sakura_sakuratts._updates import UpdateAnnouncement
    store = BundleStore(tmp_path / 'installed', lambda *_: None, lambda action: action())
    store._current = tmp_path, {'source_commit': 'old'}
    store.available = {'releaseId': 'new', 'sourceCommit': 'new', 'platform': 'macos-arm64'}
    facts = {'sessionId': 's', 'idle': True, 'activityRevision': 0, 'interactionRevision': 0}
    calls, cancelled = [], []
    now = [0]
    enabled = [True]
    def submit(request):
        calls.append(request)
        return {'accepted': True, 'operationId': 'op'}
    notice = UpdateAnnouncement(SimpleNamespace(current=lambda: facts, submit=submit, cancel=cancelled.append),
        store, lambda: enabled[0], lambda *_args, **_kwargs: None, clock=lambda: now[0])
    notice.tick()
    now[0] = 3
    facts['activityRevision'] = 1
    notice.tick()
    assert not calls
    now[0] = 6
    enabled[0] = False
    notice.tick()
    assert not calls
    enabled[0] = True
    notice.tick()
    now[0] = 9
    notice.tick()
    assert len(calls) == 1
    # No completion arrives for a failed reply; repeated ticks must not retry the model call.
    now[0] = 100
    notice.tick()
    assert len(calls) == 1 and not notice.marker.exists()
    notice.close()
    assert cancelled == ['op']
    store.close()


def test_plugin_start_checks_online_without_loading_models(tmp_path, monkeypatch):
    from plugins.builtin.sakura_sakuratts import plugin, _bundle
    p, _ = provider(tmp_path, monkeypatch)
    monkeypatch.setattr(p.bundle, 'current', lambda: None)
    checked, effects, callbacks = [], [], {}
    package = {'releaseId': 'preview-1', 'sourceCommit': 'first', 'platform': 'macos-arm64',
               'bytes': 1000, 'unpackedBytes': 2000}
    monkeypatch.setattr(_bundle, 'online_package', lambda: checked.append(True) or package)
    monkeypatch.setattr(plugin, 'Provider', lambda _: p)
    monkeypatch.setattr(p.runtime, 'start', lambda *_: pytest.fail('update check loaded models'))
    p.hub.registerProvider = lambda *_: None
    p.hub.unregisterProvider = lambda *_: None
    services = {'sakura.host.settings': SimpleNamespace(register=lambda *_args, **_kwargs: None),
                'sakura.host.settings.surface-v0': SimpleNamespace(register=lambda *_: None),
                'sakura.host.chat': SimpleNamespace(current=lambda: {'sessionId': None, 'idle': False,
                    'activityRevision': 0, 'interactionRevision': 0}, cancel=lambda _: None)}
    context = SimpleNamespace(effect=effects.append, provide=lambda *_args, **_kwargs: None,
        config=SimpleNamespace(on_change=lambda _: None, update=lambda _: None),
        on=lambda name, callback: callbacks.update({name: callback}), get=services.get)
    try:
        plugin.SakuraTTSPlugin().setup(context)
        finish_bundle_task(p.bundle)
        assert checked == [True]
        assert p.bundle.available == package
        assert 'sakura.host.chat.completed' in callbacks
    finally:
        for effect in reversed(effects):
            effect()


def test_cancel_online_install_restores_status_and_reuses_download_after_restart(tmp_path, online_repo, monkeypatch):
    _, publish = online_repo
    publish('preview-1', 'first')
    checking, resume = threading.Event(), threading.Event()
    def probe(*_):
        checking.set()
        assert resume.wait(3)
    store = BundleStore(tmp_path / 'installed', probe, lambda action: action())
    store.download()
    assert checking.wait(3)
    assert store.load()['bundle']['taskState'] == 'running'
    assert store.load()['bundle']['progress'] is None
    store.cancel()
    resume.set()
    finish_bundle_task(store)
    assert store.state == 'idle' and store.current() is None
    assert store.load()['bundle']['availableActionIds'][0] == 'installDownload'
    assert store.downloaded is not None
    store.close()
    store = BundleStore(store.directory, lambda *_: None, lambda action: action())
    def no_download(*args, **kwargs):
        raise AssertionError('完整下载不应再次访问网络')
    monkeypatch.setattr('plugins.builtin.sakura_sakuratts._bundle.urlopen', no_download)
    store.install_download()
    finish_bundle_task(store)
    assert store.state == 'succeeded', store.error
    assert store.current()[1]['source_commit'] == 'first'
    assert store.downloaded is None
    store.close()


def test_missing_character_model_returns_diagnostic_with_source_location(tmp_path):
    from types import SimpleNamespace
    import threading
    root = tmp_path / 'character'
    root.mkdir()
    (root / 'character.json').write_text(json.dumps({'extensions': {'sakura.tts.gpt-sovits': {'toneRefs': 'refs.txt'}}}))
    provider = object.__new__(Provider)
    provider.lock = threading.RLock()
    provider.closed = False
    provider.bundle = SimpleNamespace(current=lambda: True)
    provider.character = SimpleNamespace(resolve_resource=lambda *_: root / 'character.json', get=lambda _: {})
    provider.log = lambda *_args, **_kwargs: None
    result = provider.begin({'characterId': 'test', 'text': 'hello'})
    assert result['errorCode'] == 'TTS_CHARACTER_CONFIG_INVALID'
    assert '角色尚未配置GPT 模型' in result['diagnostics']['diagnostic']
    assert 'character_voice' in result['diagnostics']['exception_stack']


@pytest.mark.parametrize('seconds', [0, 60])
def test_idle_sleep_selects_engine_lifecycle(tmp_path, monkeypatch, seconds):
    from plugins.builtin.sakura_sakuratts import _runtime
    from plugins.builtin.sakura_sakuratts.plugin import configuration
    commands, requests = [], []
    def launch(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(poll=lambda: None)
    monkeypatch.setattr(_runtime.subprocess, 'Popen', launch)
    monkeypatch.setattr(_runtime, 'terminate_process_tree', lambda *args, **kwargs: None)
    runtime = _runtime.Runtime(tmp_path)
    def request(path, **kwargs):
        requests.append(path)
        return {'status': 'ready', 'state': 'sleeping'}
    monkeypatch.setattr(runtime, 'request', request)
    try:
        runtime.start((tmp_path, {'backend': 'cpu', 'backends': ['cpu']}),
                      configuration({'idleSeconds': seconds}),
                      {'gpt': 'voice.ckpt', 'sovits': 'voice.pth'}, threading.Event())
        command = commands[0]
        assert command[command.index('--runtime-mode') + 1] == ('direct' if seconds == 0 else 'managed')
        if seconds == 0:
            assert '--idle-sleep-seconds' not in command
        else:
            assert command[command.index('--idle-sleep-seconds') + 1] == '60'
        assert runtime.status() == ('ready' if seconds == 0 else 'sleeping')
        assert requests[-1] == ('/health' if seconds == 0 else '/runtime')
    finally:
        runtime.stop()


def test_runtime_lifecycle_logs_changes_without_settings_polling(tmp_path, monkeypatch):
    from plugins.builtin.sakura_sakuratts._runtime import Runtime
    logs = []
    runtime = Runtime(tmp_path, lambda level, message, **fields: logs.append(fields))
    states = iter(['waking', 'awake', 'awake', 'stopping', 'sleeping'])
    finished = [False]
    def request(path, **kwargs):
        assert path == '/runtime'
        state = next(states)
        finished[0] = state == 'sleeping'
        return {'state': state}
    monkeypatch.setattr(runtime, 'request', request)
    monkeypatch.setattr(runtime.observer_stop, 'wait', lambda seconds: finished[0])
    runtime._observe(SimpleNamespace(poll=lambda: None))
    assert [item['event'] for item in logs] == [
        'tts.engine.waking', 'tts.engine.awake', 'tts.engine.stopping', 'tts.engine.sleeping']
    runtime.observer_stop.set()
    runtime._observe(SimpleNamespace(poll=lambda: None))
    assert len(logs) == 4


@pytest.mark.parametrize('outcome', ['success', 'failed', 'cancelled'])
def test_operation_reports_terminal_result_and_preserves_failure(outcome):
    from plugins.builtin.sakura_sakuratts._diagnostics import operation
    from plugins.builtin.sakura_sakuratts._bundle import Cancelled
    records = []
    cancel = threading.Event()
    def run():
        with operation(lambda level, message, **fields: records.append((level, fields)),
                       'synthesis', cancel=cancel):
            if outcome == 'cancelled':
                cancel.set()
                raise OSError('request interrupted')
            if outcome == 'failed':
                raise RuntimeError('engine failed')
    if outcome == 'success':
        run()
    else:
        with pytest.raises(Cancelled if outcome == 'cancelled' else RuntimeError):
            run()
    assert len(records) == 1
    level, fields = records[0]
    assert fields['outcome'] == outcome
    assert fields['event'] == 'tts.operation.finished'
    assert fields['stage'] == 'synthesis'
    assert level == ('error' if outcome == 'failed' else 'info')
    assert ('diagnostic' in fields) == (outcome == 'failed')


def test_optional_device_inventory(monkeypatch):
    from plugins.builtin.sakura_sakuratts import _diagnostics as diagnostics
    monkeypatch.setattr(diagnostics.shutil, 'which', lambda name: 'fixture-nvidia-smi')
    monkeypatch.setattr(diagnostics.subprocess, 'run', lambda *args, **kwargs:
                        SimpleNamespace(stdout='NVIDIA RTX 4060, 8192, 560.10\n'))
    assert diagnostics.nvidia_device() == {
        'gpu_name': 'NVIDIA RTX 4060', 'gpu_memory_mib': 8192, 'gpu_driver': '560.10'}
    monkeypatch.setattr(diagnostics.shutil, 'which', lambda name: None)
    assert diagnostics.nvidia_device() == {}


def test_prewake_failure_includes_runtime_context_once(tmp_path, monkeypatch):
    p, _ = provider(tmp_path, monkeypatch)
    records = []
    p.logger.error = lambda message, fields: records.append(fields)
    p.runtime.backend = 'cuda'
    p.runtime.profile = 'fp16'
    p.runtime.device = {'gpu_name': 'NVIDIA RTX 4060', 'gpu_memory_mib': 8192}
    monkeypatch.setattr(p.bundle, 'current', lambda: (tmp_path, {'version': '1.0.0'}))
    def fail(*args):
        raise RuntimeError('engine unavailable')
    monkeypatch.setattr(p.runtime, 'start', fail)
    try:
        p._prewake('character', threading.Event())
        assert len(records) == 1
        assert records[0]['outcome'] == 'failed'
        assert records[0]['bundle_version'] == '1.0.0'
        assert records[0]['backend'] == 'cuda'
        assert records[0]['gpu_memory_mib'] == 8192
        assert records[0]['diagnostic'] == 'engine unavailable'
        assert 'text' not in records[0] and 'prompt_text' not in records[0]
    finally:
        p.close()
