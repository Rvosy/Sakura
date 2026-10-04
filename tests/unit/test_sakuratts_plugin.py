import io
import json
from pathlib import Path
import shutil
import threading
from types import SimpleNamespace
import zipfile

import pytest

from plugins.optional.sakura_sakuratts._bundle import BundleStore, unpack
from plugins.optional.sakura_sakuratts.plugin import Provider
from app.plugin_sdk.sakura_tools import ToolRegistry
from app.core_host.plugin_runtime_application import PluginRuntimeApplication
from app.plugins.inventory import PluginInventory
from app.storage.runtime_roots import RuntimeRoots


def bundle_archive(path, version='one', target='macos-arm64'):
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('runtime/portable.json', json.dumps({'format': 'sakuratts-portable-v1', 'has_preparation': True,
            'release': {'target': target, 'version': version, 'backends': ['mlx'], 'backend': 'mlx',
                        'python_executable': 'runtime/main/bin/python3'}}))
        z.writestr('launcher.py', '')
        z.writestr('runtime/main/bin/python3', '')
    return path


def test_import_keeps_old_environment_when_check_fails_or_cancelled(tmp_path, monkeypatch):
    monkeypatch.setattr('plugins.optional.sakura_sakuratts._bundle.platform.system', lambda: 'Darwin')
    monkeypatch.setattr('plugins.optional.sakura_sakuratts._bundle.platform.machine', lambda: 'arm64')
    store = BundleStore(tmp_path / 'installed', lambda *_: None, lambda action: action())
    store.start({'bundlePath': str(bundle_archive(tmp_path / 'one.zip'))})
    store.thread.join(3)
    original = store.current()[0]
    assert store.state == 'succeeded'
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


def test_archive_paths_cannot_escape_installation(tmp_path):
    archive = tmp_path / 'bad.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('../outside', 'bad')
    with pytest.raises(ValueError, match='路径'):
        unpack(archive, tmp_path / 'candidate', threading.Event())
    assert not (tmp_path / 'outside').exists()


def provider(tmp_path, monkeypatch):
    released = []
    services = {'sakura.host.character': object(), 'sakura.tts': SimpleNamespace(status=lambda _: {
        'enabled': True, 'providerId': 'sakura.tts.sakuratts'}),
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
        assert calls == ['start', ('/runtime/wake', {'keep_alive_seconds': 0})]
        p.config['prewake'] = False
        p.on_chat({'characterId': 'character'})
        p.executor.submit(lambda: None).result(3)
        assert len(calls) == 2
        p.config['prewake'] = True
        p.hub.status = lambda _: {'enabled': False, 'providerId': 'sakura.tts.sakuratts'}
        p.on_chat({'characterId': 'character'})
        p.executor.submit(lambda: None).result(3)
        assert len(calls) == 2
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


def test_plugin_loads_in_isolated_host_with_native_settings(tmp_path):
    repo = Path(__file__).parents[2]
    shutil.copytree(repo / 'plugins/builtin/sakura_tts_hub', tmp_path / 'plugins/builtin/sakura_tts_hub')
    shutil.copytree(repo / 'plugins/optional/sakura_sakuratts', tmp_path / 'plugins/user/sakura_sakuratts')
    config = tmp_path / 'data/plugins/sakura.tts.sakuratts/config.json'
    config.parent.mkdir(parents=True)
    config.write_text('{"enabled":true}')
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
        assert {s['sectionId'] for s in sections} == {'bundle', 'runtime'}, sections
        assert all(s['reasonCode'] == 'READY' for s in sections), sections
        section = next(s for s in sections if s['sectionId'] == 'bundle')
        assert section['actions'][0]['filePicker']['field'] == 'bundlePath'
        assert host.call_service('sakura.tts.provider.sakuratts', 'status')['available'] is False
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
