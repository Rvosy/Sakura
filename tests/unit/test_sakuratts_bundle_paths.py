import json
import os
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import zipfile

import pytest

from plugins.builtin.sakura_sakuratts import _bundle, _runtime


@pytest.mark.parametrize(('plain', 'preserve_prefix'), [
    (r'C:\Users\z\engine', False),
    (r'\\server\share\engine', False),
    ('C:\\' + 'long-directory\\' * 20, True),
])
def test_process_paths_keep_extended_prefix_only_when_needed(monkeypatch, plain, preserve_prefix):
    monkeypatch.setattr(_runtime, 'os', SimpleNamespace(name='nt'))
    extended = '\\\\?\\UNC\\' + plain[2:] if plain.startswith('\\\\') else '\\\\?\\' + plain
    assert _runtime.process_path(extended) == (extended if preserve_prefix else plain)


@pytest.mark.skipif(os.name != 'nt', reason='Windows process working directory semantics')
def test_probe_launches_from_prefixed_root_with_spaces_and_apostrophes(tmp_path):
    root = tmp_path / "z'm'f 中文/Sakura TTS"
    root.mkdir(parents=True)
    (root / 'launcher.py').write_text('''import os
from pathlib import Path
root = Path(__file__).resolve().parent
os.chdir(root)
# 上游模块使用普通路径，与继承的工作目录计算相对路径。
plain = str(root).removeprefix('\\\\\\\\?\\\\')
assert os.path.relpath(plain) == '.'
print('runtime ready')
''', encoding='utf-8')
    _runtime.probe(root, {'python_executable': sys.executable, 'backend': 'cpu'}, threading.Event())
    assert 'runtime ready' in (root / 'logs/plugin-check.log').read_text()


@pytest.fixture
def tmp_path(tmp_path):
    return Path('\\\\?\\' + str(tmp_path)) if os.name == 'nt' else tmp_path


@pytest.fixture(autouse=True)
def local_platform(monkeypatch):
    monkeypatch.setattr(_bundle, 'local_target', lambda: 'windows-x64')


def write_bundle(root):
    (root / 'runtime/main').mkdir(parents=True)
    release = {'target': 'windows-x64', 'version': 'fixture', 'backend': 'cpu',
               'python_executable': 'runtime/main/python.exe', 'source_commit': 'first', 'source_dirty': False}
    (root / 'runtime/portable.json').write_text(json.dumps({
        'format': 'sakuratts-portable-v1', 'has_preparation': True, 'release': release}), encoding='utf-8')
    (root / 'runtime/main/python.exe').write_bytes(b'interpreter fixture')
    (root / 'launcher.py').write_text('launcher fixture', encoding='utf-8')
    module = root / 'runtime/main/Lib/site-packages/torch/ao/nn/intrinsic/quantized/dynamic/modules/linear_relu.py'
    module.parent.mkdir(parents=True)
    module.write_bytes(b'class LinearReLU: pass\n')
    return release


def enable_cuda(root):
    marker = root / 'runtime/portable.json'
    value = json.loads(marker.read_text(encoding='utf-8'))
    value['release']['backends'] = ['cpu', 'cuda']
    marker.write_text(json.dumps(value), encoding='utf-8')


@pytest.mark.skipif(os.name != 'nt', reason='Windows CUDA compiler paths')
@pytest.mark.parametrize('failure', [None, 'rename', 'pointer'])
def test_unicode_default_install_moves_to_ascii_and_recovers(tmp_path, monkeypatch, failure):
    directory = tmp_path / '中文/Sakura/plugin/bundles'
    directory.mkdir(parents=True)
    storage = tmp_path / '中文/Sakura/tts/sakura'
    old = storage / 'v-existing'
    write_bundle(old)
    enable_cuda(old)
    record = {'directory': old.name, 'defaultStorage': True, 'releaseId': 'preview-1'}
    (directory / 'current.json').write_text(json.dumps(record), encoding='utf-8')
    with monkeypatch.context() as patch:
        if failure == 'rename':
            rename = Path.rename
            def fail(path, target):
                if path == old:
                    raise PermissionError('bundle busy')
                return rename(path, target)
            patch.setattr(Path, 'rename', fail)
        elif failure == 'pointer':
            patch.setattr(_bundle.BundleStore, '_write_record', lambda *_: (_ for _ in ()).throw(OSError('record busy')))
        store = store_at(directory, default_storage=storage)
        assert store.current() is not None, store.error
        assert str(store.current()[0]).isascii() == (failure != 'rename')
        assert (store.current()[0] / 'launcher.py').read_text() == 'launcher fixture'
    reopened = store_at(directory, default_storage=storage)
    assert str(reopened.current()[0]).isascii()
    assert reopened.current()[0].is_relative_to(tmp_path)
    assert reopened.release_id == 'preview-1'
    assert not old.exists()
    assert store_at(directory, default_storage=storage).current() == reopened.current()


@pytest.mark.skipif(os.name != 'nt', reason='Windows CUDA compiler paths')
def test_unicode_new_default_install_checks_ascii_location_and_keeps_custom_location(tmp_path):
    source = tmp_path / 'package'
    write_bundle(source)
    enable_cuda(source)
    archive = tmp_path / 'bundle.zip'
    with zipfile.ZipFile(archive, 'w') as zipped:
        for file in source.rglob('*'):
            if file.is_file():
                zipped.write(file, file.relative_to(source))
    checked = []
    directory, storage = tmp_path / '中文/plugin', tmp_path / '中文/tts/sakura'
    store = store_at(directory, lambda root, *_: checked.append(root), default_storage=storage)
    store.start({'bundlePath': str(archive), 'bundleDirectory': str(storage)})
    finish(store)
    assert store.state == 'succeeded', store.error
    assert str(checked[0]).isascii()
    assert store_at(directory, default_storage=storage).current() == store.current()
    custom = tmp_path / '用户指定/v-custom'
    release = write_bundle(custom)
    enable_cuda(custom)
    store._activate(custom, release, storage=custom.parent)
    assert store_at(directory, default_storage=storage).current()[0] == custom


@pytest.mark.parametrize(('output', 'fallback', 'message'), [
    (json.dumps({'passed': False, 'error_type': 'CUDARuntimeError',
                 'error': 'cudaErrorInsufficientDriver: insufficient driver'}), True, '驱动'),
    (json.dumps({'passed': False, 'error_type': 'CompileException',
                 'error': 'cannot open source file cuda/std/header.h'}), False, 'cuda/std/header.h'),
    ('RuntimeError: This preview requires an ASCII installation path', False, '迁移运行环境'),
])
def test_probe_preserves_failure_and_only_allows_device_fallback(tmp_path, output, fallback, message):
    root = tmp_path / 'engine'
    root.mkdir()
    (root / 'launcher.py').write_text(f'print({output!r})\nraise SystemExit(1)\n', encoding='utf-8')
    with pytest.raises(_runtime.RuntimeFailure) as caught:
        _runtime.probe(root, {'python_executable': sys.executable, 'backend': 'cuda'}, threading.Event())
    assert caught.value.device_unavailable == fallback
    assert message in str(caught.value)
    assert output in caught.value.diagnostics['stderr']


@pytest.mark.skipif(os.name != 'nt', reason='Windows CUDA compiler paths')
def test_cuda_start_migrates_unicode_cache_before_check(tmp_path, monkeypatch):
    from plugins.builtin.sakura_sakuratts.plugin import configuration
    source = tmp_path / '中文/runtime-data'
    model = source / 'cache/models/voice.bin'
    model.parent.mkdir(parents=True)
    model.write_bytes(b'existing converted model')
    root = tmp_path / 'ascii/v-1'
    root.mkdir(parents=True)
    runtime = _runtime.Runtime(source)
    runtime.device_checked = True
    def probe(_root, _release, _cancel, backend, cache):
        assert backend == 'cuda'
        assert str(cache).isascii()
        assert (cache / 'models/voice.bin').read_bytes() == b'existing converted model'
    monkeypatch.setattr(_runtime, 'probe', probe)
    monkeypatch.setattr(_runtime.subprocess, 'Popen', lambda *_args, **_kw: SimpleNamespace(poll=lambda: None))
    monkeypatch.setattr(_runtime, 'terminate_process_tree', lambda *_args, **_kw: None)
    monkeypatch.setattr(runtime, 'request', lambda *_args, **_kw: {})
    try:
        runtime.start((root, {'backend': 'cpu', 'backends': ['cpu', 'cuda']}),
                      configuration({'idleSeconds': 0}), {'gpt': 'voice.ckpt', 'sovits': 'voice.pth'}, threading.Event())
        assert runtime.backend == 'cuda'
        assert not source.exists()
        assert (runtime.directory / 'cache/models/voice.bin').read_bytes() == b'existing converted model'
    finally:
        runtime.stop()


@pytest.mark.skipif(os.name != 'nt', reason='Windows CUDA compiler paths')
def test_provider_reopens_migrated_cache_without_recreating_unicode_cache(tmp_path):
    from plugins.builtin.sakura_sakuratts.plugin import Provider
    plugin = tmp_path / '中文/plugin'
    storage = tmp_path / '中文/tts/sakura'
    root = storage / 'v-original'
    write_bundle(root)
    enable_cuda(root)
    bundles = plugin / 'bundles'
    bundles.mkdir(parents=True)
    (bundles / 'current.json').write_text(json.dumps({'directory': root.name, 'defaultStorage': True}))
    cache = storage / 'runtime-data/cache/voice.bin'
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b'converted model')
    logger = SimpleNamespace(**{level: lambda *_args, **_kw: None for level in ('info', 'warning', 'error')})
    services = {'sakura.host.logging': logger, 'sakura.host.storage': SimpleNamespace(resolve=lambda *_: storage)}
    context = SimpleNamespace(get=services.get, data_path=lambda _: plugin,
                              config=SimpleNamespace(get=lambda: {}))
    first = Provider(context)
    try:
        directory = first.runtime.directory
        assert str(directory).isascii()
        assert (directory / 'cache/voice.bin').read_bytes() == b'converted model'
    finally:
        first.close()
    second = Provider(context)
    try:
        assert second.runtime.directory == directory
        assert (directory / 'cache/voice.bin').read_bytes() == b'converted model'
        assert not (storage / 'runtime-data').exists()
    finally:
        second.close()


def legacy_install(tmp_path):
    directory = tmp_path / 'Sakura Desktop Pet/data/plugins/sakura.tts.sakuratts/bundles'
    root = directory / 'versions/54e64b773ac8450bafa984a82ce0ca29/SakuraTTS-windows-x64'
    write_bundle(root)
    record = {'directory': root.relative_to(directory).as_posix(), 'releaseId': 'preview-1'}
    (directory / 'current.json').write_text(json.dumps(record), encoding='utf-8')
    return directory, root, record


def store_at(directory, probe=lambda *_: None, publish=lambda action: action(), **kwargs):
    return _bundle.BundleStore(directory, probe, publish, **kwargs)


def finish(store):
    store.thread.join(5)
    assert not store.thread.is_alive()


def test_existing_install_moves_before_any_engine_check_and_preserves_data(tmp_path):
    directory, old, record = legacy_install(tmp_path)
    config = directory.parent / 'config.json'
    config.write_bytes(b'{"backend":"cuda","idleSeconds":90}')
    cache = directory.parent / 'runtime-data/cache/model.bin'
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b'user model cache')
    original = {path.relative_to(old): path.read_bytes() for path in old.rglob('*') if path.is_file()}
    with_probe = lambda *_: pytest.fail('path migration must not start the old engine')
    store = store_at(directory, with_probe)
    root, _ = store.current()
    assert len(str(root)) < len(str(old))
    assert {path.relative_to(root): path.read_bytes() for path in root.rglob('*') if path.is_file()} == original
    assert config.read_bytes() == b'{"backend":"cuda","idleSeconds":90}'
    assert cache.read_bytes() == b'user model cache'
    assert store.release_id == record['releaseId']
    assert store_at(directory).current() == store.current()


@pytest.mark.parametrize('interrupted_at', ['journal', 'rename', 'pointer', 'cleanup'])
def test_interrupted_automatic_migration_recovers_on_restart(tmp_path, monkeypatch, interrupted_at):
    directory, old, record = legacy_install(tmp_path)
    logs = []
    with monkeypatch.context() as patch:
        if interrupted_at == 'journal':
            replace = os.replace
            def fail_journal(source, destination):
                if destination == directory / 'relocation.json':
                    raise PermissionError('journal write failed')
                return replace(source, destination)
            patch.setattr(os, 'replace', fail_journal)
        elif interrupted_at == 'rename':
            rename = Path.rename
            def fail_rename(path, destination):
                if path == old:
                    raise PermissionError('file busy')
                return rename(path, destination)
            patch.setattr(Path, 'rename', fail_rename)
        elif interrupted_at == 'pointer':
            patch.setattr(_bundle.BundleStore, '_write_record', lambda *_: (_ for _ in ()).throw(OSError('pointer write failed')))
        else:
            unlink = Path.unlink
            def fail_cleanup(path, *args, **kwargs):
                if path == directory / 'relocation.json':
                    raise PermissionError('journal busy')
                return unlink(path, *args, **kwargs)
            patch.setattr(Path, 'unlink', fail_cleanup)
        first = store_at(directory, log=lambda level, message, **fields: logs.append((level, fields)))
        assert first.current() is not None, first.error
        assert not first.installation_error
        assert first.load()['bundle']['ready']
        current = first.current()[0]
        assert (current == old) == (interrupted_at in {'journal', 'rename'})
        assert (current / 'launcher.py').read_text() == 'launcher fixture'
        assert any(level == 'warning' and fields.get('diagnostic') for level, fields in logs)
        assert store_at(directory).current() == first.current()
    recovered = store_at(directory)
    assert recovered.current() is not None, recovered.error
    assert (recovered.current()[0] / 'launcher.py').read_text() == 'launcher fixture'
    assert recovered.release_id == record['releaseId']
    assert store_at(directory).current() == recovered.current()


def test_failed_migration_does_not_hide_a_broken_bundle(tmp_path, monkeypatch):
    directory, old, _ = legacy_install(tmp_path)
    (old / 'launcher.py').unlink()
    monkeypatch.setattr(Path, 'rename', lambda *_: (_ for _ in ()).throw(PermissionError('file busy')))
    store = store_at(directory)
    assert store.current() is None
    assert store.installation_error


def test_old_relocation_journal_does_not_replace_a_later_install(tmp_path):
    directory, old, record = legacy_install(tmp_path)
    store = store_at(directory)
    previous = store.current()[0]
    newer = directory / 'v-newer'
    release = write_bundle(newer)
    store._activate(newer, release, release_id='preview-2')
    (directory / 'relocation.json').write_text(json.dumps({
        'current': record, 'destination': previous.relative_to(directory).as_posix()}), encoding='utf-8')
    reopened = store_at(directory)
    assert reopened.current()[0] == newer
    assert reopened.release_id == 'preview-2'


def test_new_archive_is_flattened_before_runtime_check(tmp_path):
    root = tmp_path / 'SakuraTTS-windows-x64'
    write_bundle(root)
    archive = tmp_path / 'bundle.zip'
    with zipfile.ZipFile(archive, 'w') as zipped:
        for file in root.rglob('*'):
            if file.is_file():
                zipped.write(file, file.relative_to(tmp_path))
    checked = []
    store = store_at(tmp_path / 'bundles', lambda path, *_: checked.append(path))
    store.start({'bundlePath': str(archive)})
    finish(store)
    assert store.state == 'succeeded', store.error
    installed = store.current()[0]
    assert checked == [installed]
    assert installed.parent == store.directory
    assert (installed / 'runtime/main/python.exe').read_bytes() == b'interpreter fixture'


@pytest.mark.parametrize('outcome', ['success', 'check_failed', 'cancelled', 'pointer_failed'])
def test_external_relocation_preserves_original_until_switch_and_reopens(tmp_path, outcome, monkeypatch):
    directory, _, _ = legacy_install(tmp_path)
    store = store_at(directory)
    old = store.current()
    old_record = (directory / 'current.json').read_bytes()
    selected = tmp_path / 'tts'
    selected.mkdir()
    unrelated = selected / 'keep.txt'
    unrelated.write_bytes(b'unrelated user file')
    checked = []
    def probe(root, *_):
        checked.append(root)
        assert old[0].is_dir()
        assert store.current() == old
        assert root.is_relative_to(selected)
        if outcome == 'check_failed':
            raise RuntimeError('missing torch module')
        if outcome == 'cancelled':
            store.cancel()
    store.probe = probe
    if outcome == 'pointer_failed':
        monkeypatch.setattr(store, '_write_record', lambda *_: (_ for _ in ()).throw(OSError('cannot save current')))
    store.relocate({'bundleDirectory': str(selected)})
    finish(store)
    assert unrelated.read_bytes() == b'unrelated user file'
    assert old[0].is_dir()
    if outcome == 'success':
        assert store.state == 'succeeded', store.error
        assert store.current()[0] == checked[0]
        reopened = store_at(directory)
        assert reopened.current() == store.current()
        assert reopened.release_id == 'preview-1'
        next_root = reopened.storage_directory / 'v-next'
        release = write_bundle(next_root)
        reopened._activate(next_root, release)
        assert store_at(directory).current()[0] == next_root
        assert old[0].is_dir()
    else:
        assert store.current() == old
        assert (directory / 'current.json').read_bytes() == old_record
        assert list(selected.iterdir()) == [unrelated]
        if outcome == 'cancelled':
            assert store.state == 'idle'
        else:
            assert store.error


@pytest.mark.skipif(os.name == 'nt', reason='POSIX interpreter symlinks')
def test_external_relocation_keeps_relative_interpreter_symlink(tmp_path):
    directory, _, _ = legacy_install(tmp_path)
    store = store_at(directory)
    python = store.current()[0] / 'runtime/main/python.exe'
    python.rename(python.with_name('python-real'))
    python.symlink_to('python-real')
    store.relocate({'bundleDirectory': str(tmp_path / 'tts')})
    finish(store)
    relocated_python = store.current()[0] / 'runtime/main/python.exe'
    assert relocated_python.is_symlink()
    assert relocated_python.read_bytes() == b'interpreter fixture'


def test_reinstall_same_release_can_repair_missing_files_at_selected_location(tmp_path):
    directory, _, record = legacy_install(tmp_path)
    store = store_at(directory)
    (store.current()[0] / 'launcher.py').write_bytes(b'damaged')
    archive_root = tmp_path / 'package'
    write_bundle(archive_root)
    archive = tmp_path / 'bundle.zip'
    with zipfile.ZipFile(archive, 'w') as zipped:
        for file in archive_root.rglob('*'):
            if file.is_file():
                zipped.write(file, file.relative_to(archive_root))
    package = {'releaseId': record['releaseId'], 'sourceCommit': 'first'}
    store.available = package
    store.downloaded = archive, package
    store.reinstall({'bundleDirectory': str(tmp_path / 'tts')})
    finish(store)
    assert store.state == 'succeeded', store.error
    assert store.current()[0].is_relative_to(tmp_path / 'tts')
    assert (store.current()[0] / 'launcher.py').read_text() == 'launcher fixture'
    assert store_at(directory).current() == store.current()


@pytest.mark.parametrize('layout', ['versions', 'short'])
@pytest.mark.parametrize('failure', [None, 'rename', 'pointer'])
def test_default_storage_migrates_old_users_and_recovers(tmp_path, monkeypatch, layout, failure):
    directory, old, record = legacy_install(tmp_path)
    if layout == 'short':
        old = old.rename(directory / 'v-existing')
        record['directory'] = 'v-existing'
        (directory / 'current.json').write_text(json.dumps(record), encoding='utf-8')
    storage = tmp_path / 'tts/sakura'
    original = {p.relative_to(old): p.read_bytes() for p in old.rglob('*') if p.is_file()}
    with monkeypatch.context() as patch:
        if failure == 'rename':
            rename = Path.rename
            def fail(path, target):
                if path == old:
                    raise PermissionError('busy')
                return rename(path, target)
            patch.setattr(Path, 'rename', fail)
        elif failure == 'pointer':
            patch.setattr(_bundle.BundleStore, '_write_record', lambda *_: (_ for _ in ()).throw(OSError('record busy')))
        store = store_at(directory, default_storage=storage)
        assert store.current() is not None, store.error
        assert store.current()[0].is_relative_to(storage) == (failure != 'rename')
        assert {p.relative_to(store.current()[0]): p.read_bytes() for p in store.current()[0].rglob('*') if p.is_file()} == original
    reopened = store_at(directory, default_storage=storage)
    assert reopened.current()[0].is_relative_to(storage)
    assert reopened.release_id == 'preview-1'
    assert not old.exists()
    assert store_at(directory, default_storage=storage).current() == reopened.current()


def test_custom_storage_is_preserved_and_new_install_uses_short_default(tmp_path):
    directory = tmp_path / 'plugin/bundles'
    storage = tmp_path / 'tts/sakura'
    store = store_at(directory, default_storage=storage)
    assert store.storage_directory == storage
    external = tmp_path / 'custom/v-user'
    release = write_bundle(external)
    store._activate(external, release, storage=external.parent, release_id='custom')
    assert store_at(directory, default_storage=storage).current()[0] == external


@pytest.mark.parametrize('failure', [None, 'rename'])
def test_cache_migration_preserves_files_and_recovers(tmp_path, monkeypatch, failure):
    from plugins.builtin.sakura_sakuratts._storage import runtime_directory
    directory = tmp_path / 'plugin'
    source = directory / 'runtime-data'
    cache = source / 'cache/models/user-model.bin'
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b'user model')
    destination = tmp_path / 'tts/sakura/runtime-data'
    with monkeypatch.context() as patch:
        if failure == 'rename':
            patch.setattr(Path, 'rename', lambda *_: (_ for _ in ()).throw(PermissionError('busy')))
        active = runtime_directory(directory, destination, lambda *_args, **_fields: None)
        assert (active / 'cache/models/user-model.bin').read_bytes() == b'user model'
        assert (active == source) == (failure is not None)
    active = runtime_directory(directory, destination, lambda *_args, **_fields: None)
    assert active == destination
    assert (active / 'cache/models/user-model.bin').read_bytes() == b'user model'
    assert runtime_directory(directory, destination, lambda *_args, **_fields: None) == destination


def test_cache_migration_does_not_overwrite_existing_target(tmp_path):
    from plugins.builtin.sakura_sakuratts._storage import runtime_directory
    directory = tmp_path / 'plugin'
    source = directory / 'runtime-data'
    source.mkdir(parents=True)
    (source / 'keep').write_bytes(b'old')
    destination = tmp_path / 'tts/sakura/runtime-data'
    destination.mkdir(parents=True)
    (destination / 'keep').write_bytes(b'new')
    assert runtime_directory(directory, destination, lambda *_args, **_fields: None) == source
    assert (source / 'keep').read_bytes() == b'old'
    assert (destination / 'keep').read_bytes() == b'new'


def test_cache_migration_recovers_after_process_exits_during_move(tmp_path):
    from plugins.builtin.sakura_sakuratts._storage import runtime_directory
    directory = tmp_path / 'plugin'
    source = directory / 'runtime-data'
    source.mkdir(parents=True)
    (source / 'keep').write_bytes(b'model cache')
    destination = tmp_path / 'tts/sakura/runtime-data'
    destination.parent.mkdir(parents=True)
    source.rename(destination)
    assert runtime_directory(directory, destination, lambda *_args, **_fields: None) == destination
    assert not source.exists()
    assert (destination / 'keep').read_bytes() == b'model cache'


def test_old_shortening_journal_recovers_then_moves_to_new_default(tmp_path):
    directory, old, record = legacy_install(tmp_path)
    moved = old.rename(directory / 'v-pending')
    (directory / 'relocation.json').write_text(json.dumps({
        'current': record, 'destination': 'v-pending'}), encoding='utf-8')
    store = store_at(directory, default_storage=tmp_path / 'tts/sakura')
    assert store.current() is not None, store.error
    assert store.current()[0].is_relative_to(tmp_path / 'tts/sakura')
    assert not moved.exists()
    assert store.release_id == 'preview-1'


def test_windows_long_file_survives_default_directory_migration(tmp_path):
    if os.name != 'nt':
        pytest.skip('Windows long paths')
    directory, old, _ = legacy_install(tmp_path)
    relative = Path('runtime/preparation/Lib/site-packages/onnx/backend/test/data') / ('nested-' * 15) / 'output.pb'
    file = old / relative
    file.parent.mkdir(parents=True)
    file.write_bytes(b'long-path model data')
    assert len(str(file)) > 260
    store = store_at(directory, default_storage=tmp_path / 'tts/sakura')
    assert store.current() is not None, store.error
    assert (store.current()[0] / relative).read_bytes() == b'long-path model data'


def test_host_resolves_tts_storage_under_user_root(tmp_path):
    from app.core_host.plugin_host_services import _StorageHostService
    service = _StorageHostService(tmp_path)
    result = service.call('resolve', ['sakura.tts.sakuratts', {'scope': 'tts', 'name': 'sakura'}])
    assert Path(result['path']) == (tmp_path / 'tts/sakura').resolve()


def test_default_bundle_and_cache_follow_moved_user_root(tmp_path):
    from plugins.builtin.sakura_sakuratts._storage import runtime_directory
    user = tmp_path / 'user'
    directory, _, _ = legacy_install(user)
    storage = user / 'tts/sakura'
    store = store_at(directory, default_storage=storage)
    relative = store.current()[0].relative_to(user)
    plugin = directory.parent
    (plugin / 'runtime-data/cache').mkdir(parents=True)
    (plugin / 'runtime-data/cache/keep').write_bytes(b'user cache')
    runtime_directory(plugin, storage / 'runtime-data', lambda *_args, **_fields: None)
    moved = user.rename(tmp_path / 'moved-user')
    reopened = store_at(moved / directory.relative_to(user), default_storage=moved / 'tts/sakura')
    assert reopened.current()[0] == moved / relative
    runtime = runtime_directory(moved / plugin.relative_to(user), moved / 'tts/sakura/runtime-data',
                                lambda *_args, **_fields: None)
    assert (runtime / 'cache/keep').read_bytes() == b'user cache'
