"""离线安装到独立目录，通过运行检查后原子发布当前版本。"""
from __future__ import annotations

import json
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
import uuid
import zipfile


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
                safe_name(item.filename)
                if item.is_symlink or item.is_junction:
                    raise ValueError('整合包不能包含链接。')
        # 单独的解压进程允许取消大型 7z，也会在插件退出时回收。
        code = "import json,sys; sys.path=json.loads(sys.argv[1]); import py7zr; z=py7zr.SevenZipFile(sys.argv[2]); z.extractall(sys.argv[3]); z.close()"
        log = destination / 'extract.log'
        with log.open('wb') as output:
            process = subprocess.Popen([sys.executable, '-c', code, json.dumps(sys.path), str(archive), str(destination)],
                                       stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
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
    expected = {('Windows', 'amd64'): 'windows-x64', ('Windows', 'x86_64'): 'windows-x64',
                ('Darwin', 'arm64'): 'macos-arm64'}.get((platform.system(), platform.machine().lower()))
    if release['target'] != expected:
        raise ValueError(f"整合包平台 {release['target']} 与本机不匹配。")
    interpreter = root / release.get('python_executable', 'runtime/main/python.exe')
    if not interpreter.resolve().is_relative_to(root) or not interpreter.is_file() or not (root / 'launcher.py').is_file():
        raise ValueError('整合包缺少解释器或启动入口。')
    if not value.get('has_preparation'):
        raise ValueError('请选择含模型转换和参考音频准备组件的完整包。')
    return root, release


class BundleStore:
    def __init__(self, directory, probe, publish):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.probe, self.publish = probe, publish
        self.lock = threading.RLock()
        self.cancel_event = threading.Event()
        self.thread = None
        self.closed = False
        self.state, self.message, self.error = 'idle', '', ''

    def current(self):
        marker = self.directory / 'current.json'
        if not marker.exists():
            return None
        value = json.loads(marker.read_text(encoding='utf-8'))
        root = (self.directory / value['directory']).resolve()
        if not root.is_relative_to(self.directory.resolve()):
            raise ValueError('运行环境目录无效。')
        return inspect_bundle(root)

    def load(self):
        current = self.current()
        return {'bundlePath': '', 'bundle': {
            'applicability': 'required', 'ready': current is not None,
            'subtitle': f"{current[1]['version']} · {current[1]['target']}" if current else '未安装',
            'taskState': self.state, 'message': self.message,
            'detail': self.error[-240:], 'progress': None,
            'availableActionIds': ['cancelImport'] if self.state == 'running' else ['importBundle'],
        }}

    def start(self, values):
        archive = Path(values.get('bundlePath', ''))
        if not archive.is_absolute() or not archive.is_file():
            raise ValueError('请选择本地整合包。')
        with self.lock:
            if self.closed:
                raise RuntimeError('插件已停止。')
            if self.thread and self.thread.is_alive():
                raise ValueError('正在导入整合包。')
            self.cancel_event.clear()
            self.state, self.message, self.error = 'running', '正在解压整合包', ''
            self.thread = threading.Thread(target=self._install, args=(archive,), name='sakuratts-import', daemon=True)
            self.thread.start()
        return {}

    def _install(self, archive):
        candidate = self.directory / ('versions/' + uuid.uuid4().hex)
        published = False
        try:
            candidate.mkdir(parents=True)
            unpack(archive, candidate, self.cancel_event)
            root, release = inspect_bundle(candidate)
            self.message = '正在检查运行环境'
            self.probe(root, release, self.cancel_event)
            if self.cancel_event.is_set():
                raise Cancelled()
            # publish serializes against synthesis and configuration changes.
            self.publish(lambda: self._activate(root))
            published = True
            self.state, self.message = 'succeeded', '整合包已安装'
        except Cancelled:
            self.state, self.message = 'cancelled', '导入已取消'
        except Exception as error:
            self.state, self.message, self.error = 'failed', '导入失败，原有环境保持不变', str(error)
            (self.directory / 'import-error.log').write_text(self.error, encoding='utf-8')
        finally:
            if not published and candidate.exists():
                try:
                    shutil.rmtree(candidate)
                except OSError as error:
                    self.error += f'；临时目录清理失败：{error}'
                    self.state = 'failed'
                    self.message = '导入未完成，临时目录清理失败'


    def _activate(self, root):
        if self.cancel_event.is_set():
            raise Cancelled()
        temporary = self.directory / 'current.json.tmp'
        temporary.write_text(json.dumps({'directory': root.relative_to(self.directory).as_posix()}), encoding='utf-8')
        os.replace(temporary, self.directory / 'current.json')

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
