"""The selected interaction executor, independent of model configuration."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import yaml

from app.config.yaml_config import save_yaml_mapping
from app.storage.paths import StoragePaths


class ExecutorSettingsError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def parse_executor_selection(value: object) -> str:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or len(value) > 200
        or any(ord(character) < 32 for character in value)
    ):
        raise ExecutorSettingsError("CONFIG_DATA_INVALID", "互动方式配置无效。")
    return value


class ExecutorSettingsRepository:
    def __init__(self, user_root: Path) -> None:
        self._path = StoragePaths(user_root).system_config()

    def _read_document(self) -> dict[str, object]:
        if not self._path.exists():
            return {"config_version": 1}
        try:
            document = yaml.safe_load(self._path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, yaml.YAMLError) as error:
            raise ExecutorSettingsError("CONFIG_DATA_INVALID", "配置数据不可用。") from error
        if not isinstance(document, Mapping):
            raise ExecutorSettingsError("CONFIG_DATA_INVALID", "配置数据不可用。")
        if type(document.get("config_version")) is not int or document["config_version"] != 1:
            raise ExecutorSettingsError("CONFIG_VERSION_UNSUPPORTED", "配置版本不受支持。")
        return dict(document)

    def read(self) -> str:
        return parse_executor_selection(self._read_document().get("chat_executor", ""))

    def save(self, selection: object) -> str:
        selected = parse_executor_selection(selection)
        document = self._read_document()
        document["chat_executor"] = selected
        try:
            save_yaml_mapping(self._path, document)
        except OSError as error:
            raise ExecutorSettingsError("CONFIG_SAVE_FAILED", "互动方式未能保存。") from error
        return selected


def read_executor_selection(user_root: Path) -> str:
    return ExecutorSettingsRepository(user_root).read()
