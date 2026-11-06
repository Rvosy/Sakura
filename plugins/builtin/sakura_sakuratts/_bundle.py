"""从魔搭下载或本地导入整合包，通过运行检查后原子切换当前版本。"""
from __future__ import annotations

import json
import importlib.util
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import stat
import subprocess
import sys
import tempfile

from sakura_process import terminate_process_tree
import tarfile
import threading
import time
import zipfile
from urllib.parse import quote
from urllib.request import Request

from sakura_http import urlopen_direct_for_loopback as urlopen


REPOSITORY_URL = 'https://modelscope.cn/models/SuzushimaArisu/SakuraTTS/resolve/master/'


def filesystem_path(value):
    path = Path(value).expanduser().absolute()
    text = str(path)
    if os.name != 'nt' or text.startswith('\\\\?\\'):
        return path
    # 迁移和解压必须能访问已存在的长路径，不依赖系统启用长路径选项。
    return Path('\\\\?\\UNC\\' + text[2:] if text.startswith('\\\\') else '\\\\?\\' + text)


def local_target():
    return {('Windows', 'amd64'): 'windows-x64', ('Windows', 'x86_64'): 'windows-x64',
            ('Darwin', 'arm64'): 'macos-arm64'}.get((platform.system(), platform.machine().lower()))


def cuda_storage(directory):
    # NVRTC 不能读取中文路径中的头文件；同盘迁移复用系统重命名，不复制整合包。
    parent = next((path for path in directory.parents if str(path).isascii()), None)
    if parent is None:
        raise ValueError('CUDA 运行环境需要 ASCII 路径。请在运行环境目录中选择本地目录，例如 D:\\SakuraTTS。')
    return Path(tempfile.mkdtemp(prefix='sakura-tts-', dir=parent))


def online_package():
    target = local_target()
    if target is None:
        raise ValueError('此平台暂无 SakuraTTS 整合包。')
    request = Request(REPOSITORY_URL + 'latest-preview.json', headers={'Cache-Control': 'no-cache'})
    with urlopen(request, timeout=20) as response:
        index = json.load(response)
    packages = [p for p in index['packages'] if p['platform'] == target]
    if index['channel'] != 'preview' or len(packages) != 1:
        raise ValueError('版本索引未提供唯一的本机预览版整合包。')
    package = packages[0]
    for key in ('releaseId', 'sourceCommit'):
        if not isinstance(index[key], str) or not index[key] or package[key] != index[key]:
            raise ValueError('版本索引中的发布信息不一致。')
    path = safe_name(package['path'])
    if path.parts[:2] != ('previews', index['releaseId']) or len(path.parts) != 3:
        raise ValueError('整合包不在指定发布目录中。')
    if type(package['bytes']) is not int or package['bytes'] <= 0:
        raise ValueError('版本索引中的整合包大小无效。')
    # 下载地址由固定仓库和已校验的路径组成，不执行索引中的任意 URL。
    return {**package, 'url': REPOSITORY_URL + quote(package['path'], safe='/')}


class Cancelled(Exception):
    pass


def safe_name(name):
    path = PurePosixPath(name)
    if not name or path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name:
        raise ValueError('整合包包含不安全的文件路径。')
    return path


def unpack(archive, destination, cancel):
    def check():
        if cancel.is_set():
            raise Cancelled()
    check()
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as z:
            for item in z.infolist():
                check()
                target = destination / safe_name(item.filename)
                mode = item.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise ValueError('整合包不能包含符号链接。')
                if item.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(item) as source, target.open('wb') as out:
                        while chunk := source.read(1024 * 1024):
                            check()
                            out.write(chunk)
                    if mode & 0o111:
                        target.chmod(0o755)
    elif tarfile.is_tarfile(archive):
        with tarfile.open(archive) as tar:
            for item in tar:
                check()
                target = destination / safe_name(item.name)
                if not (item.isfile() or item.isdir()):
                    raise ValueError('整合包不能包含链接或设备文件。')
                if item.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with tar.extractfile(item) as source, target.open('wb') as out:
                        while chunk := source.read(1024 * 1024):
                            check()
                            out.write(chunk)
                    target.chmod(0o755 if item.mode & 0o111 else 0o644)
    elif archive.suffix.lower() == '.7z':
        import py7zr
        with py7zr.SevenZipFile(archive, mode='r') as z:
            for item in z.files:
                check()
                safe_name(item.filename)
                if item.is_symlink or item.is_junction:
                    raise ValueError('整合包不能包含链接。')
        # 单独的解压进程允许取消大型 7z，也会在插件退出时回收。
        code = "import json,sys; sys.path=json.loads(sys.argv[1]); import py7zr; z=py7zr.SevenZipFile(sys.argv[2]); z.extractall(sys.argv[3]); z.close()"
        executable = seven_zip_executable()
        command = ([str(executable), 'x', '-y', '-bd', '-bb0',
                    f'-o{destination.resolve()}', '--', str(archive.resolve())] if executable else
                   [sys.executable, '-c', code, json.dumps(sys.path), str(archive), str(destination)])
        log = destination / 'extract.log'
        with log.open('wb') as output:
            process = subprocess.Popen(command,
                                       stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                                       start_new_session=os.name != 'nt')
            try:
                while process.poll() is None:
                    if cancel.wait(.1):
                        raise Cancelled()
                if process.returncode:
                    raise ValueError(log.read_text(encoding='utf-8', errors='replace'))
                check()
            finally:
                terminate_process_tree(process, timeout=5)
        log.unlink()
    else:
        raise ValueError('请选择 .zip、.7z 或 .tar.gz 整合包。')


def seven_zip_executable():
    # 直接读取 wheel 自带程序，不调用可能联网下载的查找接口。
    spec = importlib.util.find_spec('py7zz')
    if spec and spec.origin:
        bundled = Path(spec.origin).parent / 'bin' / ('7zz.exe' if os.name == 'nt' else '7zz')
        if bundled.is_file():
            return bundled
    for name in ('7zz', '7z', '7za'):
        executable = shutil.which(name)
        if executable:
            return Path(executable)
    if os.name == 'nt':
        for variable in ('ProgramW6432', 'ProgramFiles', 'ProgramFiles(x86)'):
            if directory := os.environ.get(variable):
                executable = Path(directory) / '7-Zip/7z.exe'
                if executable.is_file():
                    return executable
    return None


def inspect_bundle(root):
    root = filesystem_path(root).resolve()
    marker = root / 'runtime/portable.json'
    if not marker.is_file():
        candidates = [p for p in root.iterdir() if p.is_dir() and (p / 'runtime/portable.json').is_file()]
        if len(candidates) != 1:
            raise ValueError('未找到 SakuraTTS 运行环境。')
        root = candidates[0]
        marker = root / 'runtime/portable.json'
    value = json.loads(marker.read_text(encoding='utf-8'))
    if value.get('format') != 'sakuratts-portable-v1':
        raise ValueError('不支持此整合包格式。')
    release = value['release']
    expected = local_target()
    if release['target'] != expected:
        raise ValueError(f"整合包平台 {release['target']} 与本机不匹配。")
    interpreter = root / release.get('python_executable', 'runtime/main/python.exe')
    if not interpreter.resolve().is_relative_to(root) or not interpreter.is_file() or not (root / 'launcher.py').is_file():
        raise ValueError('整合包缺少解释器或启动入口。')
    if not value.get('has_preparation'):
        raise ValueError('请选择含模型转换和参考音频准备组件的完整包。')
    return root, release


class BundleStore:
    def __init__(self, directory, probe, publish, log=lambda *_args, **_fields: None, *, default_storage=None):
        self.directory = filesystem_path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.storage_directory = self.directory
        self.probe, self.publish = probe, publish
        self.log = log
        self.default_storage = filesystem_path(default_storage) if default_storage is not None else self.directory
        self.lock = threading.RLock()
        self.cancel_event = threading.Event()
        self.thread = None
        self.closed = False
        self.state, self.message, self.error = 'idle', '', ''
        self._current = None
        self.release_id = None
        self.available = None
        self.downloaded = None
        self.progress = None
        self.installation_error = ''
        try:
            self._current = self._read_current()
            if self._current is None:
                self.default_storage.mkdir(parents=True, exist_ok=True)
                self.storage_directory = self.default_storage
        except Exception as error:
            self.installation_error = str(error)
            self.state, self.message, self.error = 'failed', '运行环境不可用，请重新导入整合包', str(error)
            self.log('error', self.message, event='tts.bundle.failed', stage='installed_bundle_read', diagnostic=str(error))
        try:
            downloaded = self.directory / 'downloads/ready.json'
            if downloaded.exists():
                package = json.loads(downloaded.read_text(encoding='utf-8'))
                archive = downloaded.parent / safe_name(package['path']).name
                if archive.is_file() and archive.stat().st_size == package['bytes']:
                    self.downloaded = archive, package
        except Exception as error:
            self.state, self.message, self.error = 'failed', '已下载整合包不可用，请重新下载', str(error)
            self.log('error', self.message, event='tts.bundle.failed', stage='downloaded_bundle_read', diagnostic=str(error))

    def current(self):
        return self._current

    def _read_current(self):
        marker = self.directory / 'current.json'
        if not marker.exists():
            return None
        value = json.loads(marker.read_text(encoding='utf-8'))
        relocation = self.directory / 'relocation.json'
        if relocation.exists():
            pending = json.loads(relocation.read_text(encoding='utf-8'))
            pending_storage = self.default_storage if pending.get('defaultStorage') else filesystem_path(pending.get('storageDirectory', self.directory))
            destination = pending_storage / pending['destination']
            if value == pending['current'] and destination.is_dir():
                value = {**value, 'directory': pending['destination']}
                if 'storageDirectory' in pending:
                    value['storageDirectory'] = pending['storageDirectory']
                    value.pop('defaultStorage', None)
                if pending.get('defaultStorage'):
                    value['defaultStorage'] = True
                try:
                    self._write_record(value)
                except OSError as error:
                    self.log('warning', '安装目录记录更新失败，继续使用已迁移的环境',
                             diagnostic=str(error), directory=str(destination))
                else:
                    self._finish_relocation()
            else:
                self._finish_relocation()
        self.storage_directory = self.default_storage if value.get('defaultStorage') else filesystem_path(value.get('storageDirectory', self.directory))
        root = (self.storage_directory / value['directory']).resolve()
        if not root.is_relative_to(self.storage_directory.resolve()):
            raise ValueError('运行环境目录无效。')
        root, release = inspect_bundle(root)
        needs_ascii = (os.name == 'nt' and 'cuda' in release.get('backends', [release['backend']]) and not str(root).isascii()
                       and 'storageDirectory' not in value)
        if needs_ascii or (self.storage_directory == self.directory and (
                self.default_storage != self.directory or Path(value['directory']).parts[0] == 'versions')):
            root = self._shorten_current(root, value, needs_ascii=needs_ascii)
        self.release_id = value.get('releaseId')
        return root, release

    def _write_record(self, value):
        temporary = self.directory / 'current.json.tmp'
        temporary.write_text(json.dumps(value), encoding='utf-8')
        os.replace(temporary, self.directory / 'current.json')

    def _shorten_current(self, root, record, *, needs_ascii=False):
        try:
            if needs_ascii:
                storage = cuda_storage(self.default_storage)
            else:
                storage = self.default_storage
                storage.mkdir(parents=True, exist_ok=True)
            destination = Path(tempfile.mkdtemp(prefix='v-', dir=storage))
            destination.rmdir()
            relative = destination.relative_to(storage).as_posix()
            updated = {**record, 'directory': relative}
            pending = {'current': record, 'destination': relative}
            if storage != self.default_storage:
                updated.pop('defaultStorage', None)
                updated['storageDirectory'] = pending['storageDirectory'] = str(storage)
            elif self.default_storage != self.directory:
                updated['defaultStorage'] = pending['defaultStorage'] = True
            relocation = self.directory / 'relocation.json'
            temporary = relocation.with_suffix('.tmp')
            temporary.write_text(json.dumps(pending), encoding='utf-8')
            os.replace(temporary, relocation)
            self.log('info', '正在调整 SakuraTTS 安装目录', event='tts.bundle.relocating',
                     source=str(root), destination=str(destination))
            # 移动后立即沿用实际目录，记录写入失败不影响本次使用。
            root = root.rename(destination)
            self.storage_directory = storage
            self._write_record(updated)
        except OSError as error:
            self.log('warning', '安装目录调整未完成，继续读取现有环境',
                     diagnostic=str(error), directory=str(root))
            return root
        self._finish_relocation()
        self.log('info', 'SakuraTTS 安装目录已调整', event='tts.bundle.relocated',
                 destination=str(destination))
        return destination

    def _finish_relocation(self):
        try:
            (self.directory / 'relocation.json').unlink()
        except OSError as error:
            self.log('warning', '安装目录迁移记录清理失败', diagnostic=str(error))

    def load(self):
        current = self.current()
        actions = []
        if self.downloaded and (not self.available or self.downloaded[1] == self.available):
            actions.append('installDownload')
        elif not self.available or not self.is_current(self.available):
            actions.append('downloadBundle')
        actions.extend(['checkUpdate', 'importBundle'])
        if current:
            actions.extend(['relocateBundle', 'reinstallBundle'])
        if self.state == 'running':
            actions = ['cancelImport']
        message = self.message
        if self.downloaded and self.state not in {'running', 'failed'}:
            downloaded_message = f"{self.downloaded[1]['releaseId']} 已下载，尚未安装"
            if downloaded_message != message:
                message = '；'.join(filter(None, (message, downloaded_message)))
        return {'bundlePath': '', 'bundleDirectory': str(self.storage_directory), 'bundle': {
            'applicability': 'required', 'ready': current is not None,
            'subtitle': f"{current[1]['version']} · {current[1]['target']}" if current else '未安装',
            'taskState': self.state, 'message': message,
            'detail': self.error, 'progress': self.progress,
            'availableActionIds': actions,
        }}

    def is_current(self, package):
        current = self.current()
        if not current:
            return False
        if self.release_id:
            return self.release_id == package['releaseId']
        release = current[1]
        return not release.get('source_dirty', True) and release.get('source_commit') == package['sourceCommit']

    def _start_task(self, operation, message, *args):
        with self.lock:
            if self.closed:
                raise RuntimeError('插件已停止。')
            if self.thread and self.thread.is_alive():
                raise ValueError('正在处理整合包。')
            self.cancel_event.clear()
            self.state, self.message, self.error = 'running', message, ''
            self.progress = None
            self.thread = threading.Thread(target=self._run_task, args=(operation, args),
                                           name='sakuratts-bundle', daemon=True)
            self.thread.start()
        return {}

    def _run_task(self, operation, args):
        try:
            operation(*args)
        except Cancelled:
            self.state, self.message = 'cancelled', '操作已取消'
        except Exception as error:
            self.state, self.message, self.error = 'failed', '整合包操作失败', str(error)
            self.log('error', self.message, event='tts.bundle.failed', diagnostic=self.error)
        finally:
            if self.state == 'cancelled':
                self.state, self.message, self.error = 'idle', '', ''
            self.progress = None

    def check_update(self, values=None):
        return self._start_task(self._check_update, '正在检查更新')

    def _check_update(self):
        self.available = None
        package = online_package()
        if self.cancel_event.is_set():
            raise Cancelled()
        self.available = package
        if self.is_current(package):
            self.message = '已是最新版本'
        else:
            self.message = (f"可下载预览版 {package['releaseId']} · {package['platform']} · 魔搭 · "
                            f"下载 {package['bytes'] / 1e9:.2f} GB")
        self.state = 'succeeded'

    def download(self, values=None):
        return self._start_task(self._download_and_install, '正在准备安装', False, self._destination(values))

    def reinstall(self, values=None):
        return self._start_task(self._download_and_install, '正在准备重新安装', True, self._destination(values))

    def _destination(self, values):
        value = (values or {}).get('bundleDirectory')
        return filesystem_path(value).resolve() if value else self.storage_directory

    def _download_and_install(self, force=False, destination=None):
        package = self.available or online_package()
        if self.cancel_event.is_set():
            raise Cancelled()
        self.available = package
        if self.is_current(package) and not force:
            self.state, self.message = 'succeeded', '已是最新版本'
            return
        if not self.downloaded or self.downloaded[1] != package:
            self._download(package)
        if self.cancel_event.is_set():
            raise Cancelled()
        self._install(*self.downloaded, destination=destination)

    def _download(self, package):
        downloads = self.directory / 'downloads'
        downloads.mkdir(exist_ok=True)
        archive = downloads / PurePosixPath(package['path']).name
        # 同一时间只保留一个待安装下载，避免数 GB 的废弃包长期占用空间。
        self.downloaded = None
        for old in downloads.iterdir():
            try:
                old.unlink()
            except OSError as error:
                if old in (archive, downloads / 'ready.json'):
                    raise
                self.log('warning', '旧下载文件清理失败', diagnostic=str(error))
        completed = False
        received = 0
        started = time.monotonic()
        try:
            with urlopen(Request(package['url']), timeout=20) as response, archive.open('wb') as output:
                while True:
                    if self.cancel_event.is_set():
                        raise Cancelled()
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > package['bytes']:
                        raise ValueError('下载大小超过版本索引记录。')
                    output.write(chunk)
                    self.progress = received * 100 // package['bytes']
                    speed = received / max(time.monotonic() - started, .001)
                    self.message = (f"正在下载 · {received / 1e6:.1f} / {package['bytes'] / 1e6:.1f} MB · "
                                    f"{speed / 1e6:.1f} MB/s · 剩余约 {int((package['bytes'] - received) / speed)} 秒")
            if self.cancel_event.is_set():
                raise Cancelled()
            if received != package['bytes']:
                raise ValueError(f"下载不完整：收到 {received} 字节，预期 {package['bytes']} 字节。")
            (downloads / 'ready.json').write_text(json.dumps(package), encoding='utf-8')
            self.downloaded = archive, package
            completed = True
            self.message = '正在解压整合包'
            self.progress = None
        finally:
            if not completed:
                archive.unlink(missing_ok=True)

    def install_download(self, values=None):
        with self.lock:
            if self.downloaded is None:
                raise ValueError('请先下载整合包。')
            archive, package = self.downloaded
            return self._start_task(self._install, '正在解压整合包', archive, package, self._destination(values))

    def start(self, values):
        archive = filesystem_path(values['bundlePath'])
        return self._start_task(self._install, '正在解压整合包', archive, None, self._destination(values))

    def relocate(self, values):
        destination = filesystem_path(values['bundleDirectory']).resolve()
        return self._start_task(self._relocate, '正在迁移运行环境', destination)

    def _relocate(self, destination):
        source, release = self.current()
        if destination.is_relative_to(source):
            raise ValueError('迁移目标不能放在正在迁移的整合包内部。')
        destination.mkdir(parents=True, exist_ok=True)
        storage = Path(tempfile.mkdtemp(prefix='sakura-tts-', dir=destination))
        candidate = storage / 'v-1'
        published = False
        started_at = time.monotonic()
        try:
            self.log('info', '正在迁移 SakuraTTS 运行环境', event='tts.bundle.relocating',
                     source=str(source), destination=str(candidate))

            def copy_file(source_file, target_file):
                if self.cancel_event.is_set():
                    raise Cancelled()
                with open(source_file, 'rb') as reader, open(target_file, 'wb') as writer:
                    while chunk := reader.read(1024 * 1024):
                        if self.cancel_event.is_set():
                            raise Cancelled()
                        writer.write(chunk)
                shutil.copystat(source_file, target_file)
                return target_file

            # 复制期间旧服务仍可使用；检查与切换复用安装入口。
            shutil.copytree(source, candidate, copy_function=copy_file, symlinks=True)
            self.message = '正在检查迁移后的运行环境'
            self.probe(candidate, release, self.cancel_event)
            self.publish(lambda: self._activate(candidate, release, storage=storage, release_id=self.release_id))
            published = True
            self.state, self.message, self.error = 'succeeded', '', ''
            self.log('info', 'SakuraTTS 运行环境已迁移', event='tts.bundle.relocated',
                     destination=str(candidate), elapsed_ms=round((time.monotonic() - started_at) * 1000))
        finally:
            if not published:
                try:
                    shutil.rmtree(storage)
                except OSError as error:
                    self.log('warning', '迁移临时目录清理失败', diagnostic=str(error), directory=str(storage))

    def _install(self, archive, package=None, destination=None):
        started_at = time.monotonic()
        self.message = '正在解压整合包'
        self.progress = None
        storage = self.storage_directory
        if destination is not None and destination != storage:
            destination.mkdir(parents=True, exist_ok=True)
            storage = Path(tempfile.mkdtemp(prefix='sakura-tts-', dir=destination))
        candidate = Path(tempfile.mkdtemp(prefix='v-', dir=storage))
        published = False
        try:
            self.log('info', '正在解压 SakuraTTS 整合包', event='tts.bundle.extracting')
            unpack(archive, candidate, self.cancel_event)
            root, release = inspect_bundle(candidate)
            if root != candidate:
                flattened = Path(tempfile.mkdtemp(prefix='v-', dir=storage))
                flattened.rmdir()
                root.rename(flattened)
                previous_candidate, candidate, root = candidate, flattened, flattened
                shutil.rmtree(previous_candidate)
            if package and (release.get('source_commit') != package['sourceCommit'] or release.get('source_dirty', True)):
                raise ValueError('整合包源码版本与下载索引不一致。')
            if (os.name == 'nt' and 'cuda' in release.get('backends', [release['backend']]) and not str(root).isascii()
                    and storage == self.default_storage):
                storage = cuda_storage(storage)
                candidate = root.rename(storage / root.name)
                root = candidate
            self.message = '正在检查运行环境'
            self.log('info', '正在检查 SakuraTTS 运行环境', event='tts.bundle.checking')
            self.probe(root, release, self.cancel_event)
            if self.cancel_event.is_set():
                raise Cancelled()
            # publish serializes against synthesis and configuration changes.
            self.publish(lambda: self._activate(root, release, package, storage=storage))
            published = True
            self.state, self.message, self.error = 'succeeded', '', ''
            if package:
                self.downloaded = None
                try:
                    (archive.parent / 'ready.json').unlink(missing_ok=True)
                    archive.unlink(missing_ok=True)
                except OSError as error:
                    self.log('warning', '整合包已安装，下载文件清理失败', diagnostic=str(error))
        except Cancelled:
            self.state, self.message = 'cancelled', '导入已取消'
        except Exception as error:
            self.state, self.message, self.error = 'failed', '导入失败，原有环境保持不变', str(error)
        finally:
            if not published and candidate.exists():
                try:
                    shutil.rmtree(candidate)
                    if storage != self.storage_directory:
                        storage.rmdir()
                except OSError as error:
                    self.error += f'；临时目录清理失败：{error}'
                    self.state = 'failed'
                    self.message = '导入未完成，临时目录清理失败'
            self.log('error' if self.state == 'failed' else 'info',
                     {'succeeded': 'SakuraTTS 整合包已导入', 'cancelled': 'SakuraTTS 整合包导入已取消',
                      'failed': self.message}[self.state],
                     event='tts.bundle.' + self.state,
                     elapsed_ms=round((time.monotonic() - started_at) * 1000),
                     **({'diagnostic': self.error} if self.state == 'failed' else {}))


    def _activate(self, root, release, package=None, *, storage=None, release_id=None):
        if self.cancel_event.is_set():
            raise Cancelled()
        retained = [root]
        if self._current:
            retained.append(self._current[0])
        storage = storage or self.storage_directory
        release_id = package['releaseId'] if package else release_id
        record = {'directory': root.relative_to(storage).as_posix(), 'releaseId': release_id}
        if storage == self.default_storage and storage != self.directory:
            record['defaultStorage'] = True
        elif storage != self.directory:
            record['storageDirectory'] = str(storage)
        self._write_record(record)
        self.storage_directory = storage
        self._current = root, release
        self.release_id = release_id
        self.installation_error = ''
        try:
            versions = [*storage.glob('v-*'), *(storage / 'versions').glob('*')]
            for version in versions:
                if version.is_dir() and not any(path.is_relative_to(version) for path in retained):
                    shutil.rmtree(version)
        except OSError as error:
            self.log('warning', '整合包已安装，旧版本目录清理失败', diagnostic=str(error))

    def cancel(self, values=None):
        self.cancel_event.set()
        return {}

    def close(self):
        with self.lock:
            self.closed = True
            self.cancel()
            thread = self.thread
        if thread:
            thread.join()
