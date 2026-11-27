"""Shared storage directories for plugin-managed resources."""
from pathlib import Path


def tts_directories(context):
    try:
        storage = context.get("sakura.host.storage")
    except KeyError:
        storage = None

    def resolve(name: str) -> Path:
        if storage is not None:
            try:
                return Path(storage.resolve("tts", name))
            except Exception as error:
                if getattr(error, "code", None) != "SERVICE_MISSING":
                    raise
        return Path(context.data_path(name))

    return resolve
