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

from sakura_process import terminate_process_tree
import tarfile
import threading
import time
import uuid
import zipfile
from urllib.parse import quote
from urllib.request import Request

from sakura_http import urlopen_direct_for_loopback as urlopen


REPOSITORY_URL = 'https://modelscope.cn/models/SuzushimaArisu/SakuraTTS/resolve/master/'


def local_target():
    return {('Windows', 'amd64'): 'windows-x64', ('Windows', 'x86_64'): 'windows-x64',
            ('Darwin', 'arm64'): 'macos-arm64'}.get((platform.system(), platform.machine().lower()))


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
    for key in ('bytes', 'unpackedBytes'):
        if type(package[key]) is not int or package[key] <= 0:
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
                    raise ValueError(log.read_text(encoding='utf-8', errors='replace')[-4000:])
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
    root = Path(root).resolve()
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
    def __init__(self, directory, probe, publish, log=lambda *_args, **_fields: None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.probe, self.publish = probe, publish
        self.log = log
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
        root = (self.directory / value['directory']).resolve()
        if not root.is_relative_to(self.directory.resolve()):
            raise ValueError('运行环境目录无效。')
        current = inspect_bundle(root)
        self.release_id = value.get('releaseId')
        return current

    def load(self):
        current = self.current()
        actions = []
        if self.downloaded and (not self.available or self.downloaded[1] == self.available):
            actions.append('installDownload')
        elif not self.available or not self.is_current(self.available):
            actions.append('downloadBundle')
        actions.extend(['checkUpdate', 'importBundle'])
        if self.state == 'running':
            actions = ['cancelImport']
        message = self.message
        if self.downloaded and self.state not in {'running', 'failed'}:
            downloaded_message = f"{self.downloaded[1]['releaseId']} 已下载，尚未安装"
            if downloaded_message != message:
                message = '；'.join(filter(None, (message, downloaded_message)))
        return {'bundlePath': '', 'bundle': {
            'applicability': 'required', 'ready': current is not None,
            'subtitle': f"{current[1]['version']} · {current[1]['target']}" if current else '未安装',
            'taskState': self.state, 'message': message[:240],
            'detail': self.error[-240:], 'progress': self.progress,
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
                            f"下载 {package['bytes'] / 1e9:.2f} GB · 解压 {package['unpackedBytes'] / 1e9:.2f} GB")
        self.state = 'succeeded'

    def download(self, values=None):
        with self.lock:
            return self._start_task(self._download_and_install, '正在准备安装')

    def _download_and_install(self):
        package = self.available or online_package()
        if self.cancel_event.is_set():
            raise Cancelled()
        self.available = package
        if self.is_current(package):
            self.state, self.message = 'succeeded', '已是最新版本'
            return
        if not self.downloaded or self.downloaded[1] != package:
            self._download(package)
        if self.cancel_event.is_set():
            raise Cancelled()
        self._install(*self.downloaded)

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
        if shutil.disk_usage(self.directory).free < package['bytes'] + package['unpackedBytes']:
            raise ValueError('磁盘空间不足以下载和解压整合包。')
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
            return self._start_task(self._install, '正在解压整合包', archive, package)

    def start(self, values):
        archive = Path(values.get('bundlePath', ''))
        if not archive.is_absolute() or not archive.is_file():
            raise ValueError('请选择本地整合包。')
        return self._start_task(self._install, '正在解压整合包', archive)

    def _install(self, archive, package=None):
        started_at = time.monotonic()
        self.message = '正在解压整合包'
        self.progress = None
        candidate = self.directory / ('versions/' + uuid.uuid4().hex)
        published = False
        try:
            if package and shutil.disk_usage(self.directory).free < package['unpackedBytes']:
                raise ValueError('磁盘空间不足以解压整合包。')
            self.log('info', '正在解压 SakuraTTS 整合包', event='tts.bundle.extracting')
            candidate.mkdir(parents=True)
            unpack(archive, candidate, self.cancel_event)
            root, release = inspect_bundle(candidate)
            if package and (release.get('source_commit') != package['sourceCommit'] or release.get('source_dirty', True)):
                raise ValueError('整合包源码版本与下载索引不一致。')
            self.message = '正在检查运行环境'
            self.log('info', '正在检查 SakuraTTS 运行环境', event='tts.bundle.checking')
            self.probe(root, release, self.cancel_event)
            if self.cancel_event.is_set():
                raise Cancelled()
            # publish serializes against synthesis and configuration changes.
            self.publish(lambda: self._activate(root, release, package))
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


    def _activate(self, root, release, package=None):
        if self.cancel_event.is_set():
            raise Cancelled()
        retained = [root]
        if self._current:
            retained.append(self._current[0])
        temporary = self.directory / 'current.json.tmp'
        release_id = package['releaseId'] if package else None
        temporary.write_text(json.dumps({'directory': root.relative_to(self.directory).as_posix(),
                                         'releaseId': release_id}), encoding='utf-8')
        os.replace(temporary, self.directory / 'current.json')
        self._current = root, release
        self.release_id = release_id
        self.installation_error = ''
        try:
            for version in (self.directory / 'versions').iterdir():
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
