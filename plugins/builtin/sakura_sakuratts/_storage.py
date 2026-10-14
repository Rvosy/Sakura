"""将引擎缓存移到用户数据根下的短目录，失败时沿用原目录。"""
from __future__ import annotations

try:
    from ._bundle import filesystem_path
except ImportError:
    from _bundle import filesystem_path


def runtime_directory(directory, destination, log):
    directory, destination = filesystem_path(directory), filesystem_path(destination)
    source = directory / 'runtime-data'
    try:
        if not source.exists():
            destination.mkdir(parents=True, exist_ok=True)
            return destination
        if destination.exists():
            log('warning', '迁移目标已有数据，继续使用原缓存', source=str(source), destination=str(destination))
            return source
        destination.parent.mkdir(parents=True, exist_ok=True)
        log('info', '正在迁移 SakuraTTS 引擎缓存', source=str(source), destination=str(destination))
        source.rename(destination)
        log('info', 'SakuraTTS 引擎缓存已迁移', destination=str(destination))
        return destination
    except OSError as error:
        log('warning', '引擎缓存迁移未完成，继续使用原目录', diagnostic=str(error), directory=str(source))
        return source
