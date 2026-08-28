from __future__ import annotations

import builtins
import hashlib
import http.client
import os
import socket
import shutil
import stat
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.config import core_config_reader as reader_module
from app.config.core_config_reader import (
    SUPPORTED_CORE_CONFIG_VERSION,
    CoreConfigReadResult,
    CoreConfigReader,
    ProviderSelection,
    StableReadinessError,
)
from app.llm.api_client import ApiSettings


FIXTURE_ROOT = (
    Path(__file__).parents[1] / "fixtures" / "runtime_v2" / "wp_3_01" / "ready"
)
APPROVED_CONFIG_READS = {
    "config/system_config.yaml",
    "config/api.yaml",
    "config/characters.yaml",
    "data/chat_history/sakura.jsonl",
}
EXPECTED_PROBLEMS = {
    "CORE_CONFIG_SETUP_REQUIRED": StableReadinessError(
        state="setup_required",
        code="CORE_CONFIG_SETUP_REQUIRED",
        message="Core configuration setup is required.",
    ),
    "CONFIG_DATA_INVALID": StableReadinessError(
        state="failed",
        code="CONFIG_DATA_INVALID",
        message="Core configuration data is invalid.",
    ),
    "CONFIG_VERSION_UNSUPPORTED": StableReadinessError(
        state="failed",
        code="CONFIG_VERSION_UNSUPPORTED",
        message="Core configuration version is unsupported.",
    ),
    "PROVIDER_SETUP_REQUIRED": StableReadinessError(
        state="setup_required",
        code="PROVIDER_SETUP_REQUIRED",
        message="Provider configuration setup is required.",
    ),
}
_REAL_PATH_OPEN = Path.open


def _fresh_root(tmp_path: Path) -> Path:
    root = tmp_path / "app-root"
    shutil.copytree(FIXTURE_ROOT, root)
    return root


def _snapshot(root: Path) -> dict[str, dict[str, object]]:
    snapshot: dict[str, dict[str, object]] = {}
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        relative_path = path.relative_to(root).as_posix()
        with _REAL_PATH_OPEN(path, "rb") as handle:
            content = handle.read()
        metadata = path.stat()
        snapshot[relative_path] = {
            "relative_path": relative_path,
            "bytes": content,
            "size": metadata.st_size,
            "mtime_ns": metadata.st_mtime_ns,
            "sha256": hashlib.sha256(content).hexdigest(),
            "mode": stat.S_IMODE(metadata.st_mode),
            "bak_absent": not Path(f"{path}.bak").exists(),
        }
    return snapshot


def _fail_write(operation: str):  # type: ignore[no-untyped-def]
    def fail(*_args: object, **_kwargs: object) -> None:
        pytest.fail(f"strict read-only reader attempted {operation}")

    return fail


def _read_with_guards(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[CoreConfigReadResult, list[tuple[str, str, str | None]]]:
    before = _snapshot(root)
    opened: list[tuple[str, str, str | None]] = []

    def guarded_path_open(
        path: Path,
        mode: str = "r",
        buffering: int = -1,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ):  # type: ignore[no-untyped-def]
        if any(flag in mode for flag in "wax+"):
            pytest.fail(f"strict read-only reader attempted Path.open mode {mode!r}")
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError:
            relative = f"OUTSIDE_ROOT:{path.name}"
        opened.append((relative, mode, encoding))
        return _REAL_PATH_OPEN(path, mode, buffering, encoding, errors, newline)

    real_builtin_open = builtins.open

    def guarded_builtin_open(
        file: Any,
        mode: str = "r",
        *args: object,
        **kwargs: object,
    ):  # type: ignore[no-untyped-def]
        if any(flag in mode for flag in "wax+"):
            pytest.fail(f"strict read-only reader attempted open mode {mode!r}")
        if isinstance(file, (str, os.PathLike)):
            path = Path(file)
            try:
                relative = path.relative_to(root).as_posix()
            except ValueError:
                relative = f"OUTSIDE_ROOT:{path.name}"
            opened.append((relative, mode, kwargs.get("encoding")))
        return real_builtin_open(file, mode, *args, **kwargs)

    with monkeypatch.context() as guards:
        for method_name in (
            "write_text",
            "write_bytes",
            "touch",
            "mkdir",
            "rename",
            "replace",
            "unlink",
            "chmod",
        ):
            guards.setattr(Path, method_name, _fail_write(f"Path.{method_name}"))
        guards.setattr(Path, "open", guarded_path_open)
        guards.setattr(builtins, "open", guarded_builtin_open)
        for function_name in ("rename", "replace", "unlink", "mkdir", "chmod"):
            guards.setattr(os, function_name, _fail_write(f"os.{function_name}"))

        result = CoreConfigReader().read(root)

    after = _snapshot(root)
    assert after == before
    assert all(item["bak_absent"] for item in before.values())
    assert not list(root.rglob("*.bak"))
    assert {relative for relative, _mode, _encoding in opened} <= APPROVED_CONFIG_READS
    assert all(
        (mode == "r" and encoding == "utf-8")
        if path.startswith("config/")
        else (mode == "rb" and encoding is None)
        for path, mode, encoding in opened
    )
    return result, opened


def _assert_problem(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    code: str,
) -> None:
    result, _opened = _read_with_guards(root, monkeypatch)
    assert result == CoreConfigReadResult(
        current_character_id=None,
        provider_selection=None,
        config_problem=EXPECTED_PROBLEMS[code],
    )
    assert result.config_problem is not None
    assert result.config_problem.retryable is False
    assert str(root) not in result.config_problem.message


@pytest.mark.parametrize(
    ("content", "code"),
    [
        (None, "CORE_CONFIG_SETUP_REQUIRED"),
        ("", "CONFIG_DATA_INVALID"),
        ("config_version: [\n", "CONFIG_DATA_INVALID"),
        ("- config_version\n- 1\n", "CONFIG_DATA_INVALID"),
        ("other: value\n", "CONFIG_VERSION_UNSUPPORTED"),
        ("config_version: true\n", "CONFIG_VERSION_UNSUPPORTED"),
        ("config_version: 0\n", "CONFIG_VERSION_UNSUPPORTED"),
        ("config_version: 2\n", "CONFIG_VERSION_UNSUPPORTED"),
    ],
    ids=[
        "missing",
        "zero",
        "bad-yaml",
        "nonmapping",
        "missing-version",
        "bool-version",
        "old-version",
        "future-version",
    ],
)
def test_system_config_frozen_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    content: str | None,
    code: str,
) -> None:
    root = _fresh_root(tmp_path)
    path = root / "config" / "system_config.yaml"
    if content is None:
        path.unlink()
    else:
        path.write_text(content, encoding="utf-8")

    if content is None:
        result, _opened = _read_with_guards(root, monkeypatch)
        assert result.config_problem is None
        assert result.provider_selection is not None
        return

    _assert_problem(root, monkeypatch, code)


@pytest.mark.parametrize("file_name", ["api.yaml", "characters.yaml"])
@pytest.mark.parametrize(
    ("content", "code"),
    [
        (None, "CORE_CONFIG_SETUP_REQUIRED"),
        ("", "CORE_CONFIG_SETUP_REQUIRED"),
        ("  \n\t", "CORE_CONFIG_SETUP_REQUIRED"),
        ("null\n", "CORE_CONFIG_SETUP_REQUIRED"),
        ("{}\n", "CORE_CONFIG_SETUP_REQUIRED"),
        ("broken: [\n", "CONFIG_DATA_INVALID"),
        ("- item\n", "CONFIG_DATA_INVALID"),
        ("configured\n", "CONFIG_DATA_INVALID"),
        ("true\n", "CONFIG_DATA_INVALID"),
    ],
    ids=[
        "missing",
        "zero",
        "blank",
        "null",
        "empty-mapping",
        "bad-yaml",
        "list",
        "scalar",
        "bool",
    ],
)
def test_auxiliary_config_frozen_file_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    file_name: str,
    content: str | None,
    code: str,
) -> None:
    root = _fresh_root(tmp_path)
    path = root / "config" / file_name
    if content is None:
        path.unlink()
    else:
        path.write_text(content, encoding="utf-8")

    if file_name == "characters.yaml" and code == "CORE_CONFIG_SETUP_REQUIRED":
        result, _opened = _read_with_guards(root, monkeypatch)
        assert result.current_character_id is None
        assert result.provider_selection is not None
        assert result.config_problem is None
        return
    if file_name == "api.yaml" and code == "CORE_CONFIG_SETUP_REQUIRED":
        code = "PROVIDER_SETUP_REQUIRED"
    _assert_problem(root, monkeypatch, code)


VALID_API_PREFIX = """\
api_profiles:
  - id: fixture
    alias: Fixture Provider
    base_url: https://fixture.invalid/v1
    api_key: REDACTED_FIXTURE_API_KEY
    models:
      - name: fixture-model
model_slots:
  chat:
    profile_id: fixture
    model: fixture-model
"""


@pytest.mark.parametrize(
    "content",
    [
        "api_profiles: {}\nmodel_slots: {}\n",
        "api_profiles: null\nmodel_slots: {chat: {profile_id: fixture, model: fixture-model}}\n",
        "api_profiles: [null]\nmodel_slots: {}\n",
        "api_profiles: [{id: 1, alias: ok, base_url: https://fixture.invalid, api_key: key, models: [model]}]\nmodel_slots: {}\n",
        "api_profiles: [{id: fixture, alias: [], base_url: https://fixture.invalid, api_key: key, models: [model]}]\nmodel_slots: {}\n",
        "api_profiles: [{id: fixture, alias: ok, base_url: [], api_key: key, models: [model]}]\nmodel_slots: {}\n",
        "api_profiles: [{id: fixture, alias: ok, base_url: https://fixture.invalid, api_key: 1, models: [model]}]\nmodel_slots: {}\n",
        "api_profiles: [{id: fixture, alias: ok, base_url: https://fixture.invalid, api_key: key, models: {name: model}}]\nmodel_slots: {}\n",
        "api_profiles: [{id: fixture, alias: ok, base_url: https://fixture.invalid, api_key: key, models: null}]\nmodel_slots: {}\n",
        "api_profiles: [{id: fixture, alias: ok, base_url: https://fixture.invalid, api_key: key, models: [1]}]\nmodel_slots: {}\n",
        "api_profiles: [{id: fixture, alias: ok, base_url: https://fixture.invalid, api_key: key, models: [{name: 1}]}]\nmodel_slots: {}\n",
        "api_profiles: []\nmodel_slots: []\n",
        "api_profiles: [{id: fixture, alias: ok, base_url: https://fixture.invalid, api_key: key, models: [fixture-model]}]\nmodel_slots: null\n",
        "api_profiles: []\nmodel_slots: {chat: []}\n",
        "api_profiles: [{id: fixture, alias: ok, base_url: https://fixture.invalid, api_key: key, models: [fixture-model]}]\nmodel_slots: {chat: null}\n",
        "api_profiles: []\nmodel_slots: {chat: {profile_id: 1, model: fixture-model}}\n",
        "api_profiles: []\nmodel_slots: {chat: {profile_id: fixture, model: []}}\n",
        f"{VALID_API_PREFIX}  vision_chat: []\n",
        f"{VALID_API_PREFIX}  vision_chat: null\n",
    ],
    ids=[
        "profiles-container",
        "profiles-null",
        "profile-entry",
        "profile-id-type",
        "profile-alias-type",
        "profile-url-type",
        "profile-key-type",
        "models-container",
        "models-null",
        "model-entry-type",
        "model-name-type",
        "slots-container",
        "slots-null",
        "chat-container",
        "chat-null",
        "slot-profile-type",
        "slot-model-type",
        "optional-slot-container",
        "optional-slot-null",
    ],
)
def test_api_malformed_container_or_field_is_data_invalid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    content: str,
) -> None:
    root = _fresh_root(tmp_path)
    (root / "config" / "api.yaml").write_text(content, encoding="utf-8")
    _assert_problem(root, monkeypatch, "CONFIG_DATA_INVALID")


def test_core_reader_ignores_legacy_memory_model_slot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _fresh_root(tmp_path)
    (root / "config" / "api.yaml").write_text(
        f"{VALID_API_PREFIX}  memory_curation: {{profile_id: fixture, model: 1}}\n",
        encoding="utf-8",
    )

    result, _opened = _read_with_guards(root, monkeypatch)

    assert result.config_problem is None
    assert result.provider_selection is not None
    assert result.provider_selection.api_settings.model == "fixture-model"


@pytest.mark.parametrize(
    "content",
    [
        "model_slots: {chat: {profile_id: fixture, model: fixture-model}}\n",
        "api_profiles: []\nmodel_slots: {chat: {profile_id: fixture, model: fixture-model}}\n",
        "api_profiles: [{id: '', alias: ok, base_url: https://fixture.invalid, api_key: key, models: [{name: fixture-model}]}]\nmodel_slots: {chat: {profile_id: fixture, model: fixture-model}}\n",
        "api_profiles: [{id: fixture, alias: ok, base_url: '', api_key: key, models: [{name: fixture-model}]}]\nmodel_slots: {chat: {profile_id: fixture, model: fixture-model}}\n",
        "api_profiles: [{id: fixture, alias: ok, base_url: https://fixture.invalid, api_key: '', models: [{name: fixture-model}]}]\nmodel_slots: {chat: {profile_id: fixture, model: fixture-model}}\n",
        "api_profiles: [{id: fixture, alias: ok, base_url: https://fixture.invalid, api_key: key, models: []}]\nmodel_slots: {chat: {profile_id: fixture, model: fixture-model}}\n",
        "api_profiles: [{id: fixture, alias: ok, base_url: https://fixture.invalid, api_key: key, models: [{name: fixture-model}]}]\n",
        "api_profiles: [{id: fixture, alias: ok, base_url: https://fixture.invalid, api_key: key, models: [{name: fixture-model}]}]\nmodel_slots: {}\n",
        "api_profiles: [{id: fixture, alias: ok, base_url: https://fixture.invalid, api_key: key, models: [{name: fixture-model}]}]\nmodel_slots: {chat: {}}\n",
        "api_profiles: [{id: fixture, alias: ok, base_url: https://fixture.invalid, api_key: key, models: [{name: fixture-model}]}]\nmodel_slots: {chat: {profile_id: other, model: fixture-model}}\n",
        "api_profiles: [{id: fixture, alias: ok, base_url: https://fixture.invalid, api_key: key, models: [{name: fixture-model}]}]\nmodel_slots: {chat: {profile_id: fixture, model: other-model}}\n",
        "api_profiles: [{id: fixture, alias: ok, base_url: ftp://fixture.invalid, api_key: key, models: [{name: fixture-model}]}]\nmodel_slots: {chat: {profile_id: fixture, model: fixture-model}}\n",
        "api_profiles: [{id: fixture, alias: ok, base_url: https:///v1, api_key: key, models: [{name: fixture-model}]}]\nmodel_slots: {chat: {profile_id: fixture, model: fixture-model}}\n",
        "api_profiles: [{id: fixture, alias: ok, base_url: 'https://bad host/v1', api_key: key, models: [{name: fixture-model}]}]\nmodel_slots: {chat: {profile_id: fixture, model: fixture-model}}\n",
        "api_profiles: [{id: fixture, alias: ok, base_url: 'https://-bad.invalid/v1', api_key: key, models: [{name: fixture-model}]}]\nmodel_slots: {chat: {profile_id: fixture, model: fixture-model}}\n",
        "api_profiles: [{id: fixture, alias: ok, base_url: 'https://[bad', api_key: key, models: [{name: fixture-model}]}]\nmodel_slots: {chat: {profile_id: fixture, model: fixture-model}}\n",
    ],
    ids=[
        "profiles-missing",
        "profiles-empty",
        "profile-id-empty",
        "base-url-empty",
        "api-key-empty",
        "models-empty",
        "slots-missing",
        "chat-slot-missing",
        "chat-fields-missing",
        "profile-mismatch",
        "model-mismatch",
        "url-scheme-invalid",
        "url-host-missing",
        "url-host-whitespace",
        "url-host-label-invalid",
        "url-malformed",
    ],
)
def test_valid_api_mapping_without_usable_chat_provider_requires_setup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    content: str,
) -> None:
    root = _fresh_root(tmp_path)
    (root / "config" / "api.yaml").write_text(content, encoding="utf-8")
    _assert_problem(root, monkeypatch, "PROVIDER_SETUP_REQUIRED")


def test_unused_incomplete_provider_does_not_invalidate_selected_chat_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _fresh_root(tmp_path)
    (root / "config" / "api.yaml").write_text(
        """\
api_profiles:
  - id: fixture
    alias: Fixture Provider
    base_url: https://fixture.invalid/v1
    api_key: REDACTED_FIXTURE_API_KEY
    models:
      - name: fixture-model
  - id: draft-provider
    alias: Draft Provider
    base_url: https://draft.invalid/v1
    api_key: ''
    models: []
model_slots:
  chat:
    profile_id: fixture
    model: fixture-model
""",
        encoding="utf-8",
    )

    result, _opened = _read_with_guards(root, monkeypatch)

    assert result.config_problem is None
    assert result.current_character_id == "sakura"
    assert result.provider_selection is not None
    assert result.provider_selection.api_settings.model == "fixture-model"


@pytest.mark.parametrize(
    ("content", "code"),
    [
        ("other: configured\n", "CORE_CONFIG_SETUP_REQUIRED"),
        ("current_character_id: null\n", "CORE_CONFIG_SETUP_REQUIRED"),
        ("current_character_id: ''\n", "CORE_CONFIG_SETUP_REQUIRED"),
        ("current_character_id: '   '\n", "CORE_CONFIG_SETUP_REQUIRED"),
        ("current_character_id: 7\n", "CONFIG_DATA_INVALID"),
        ("current_character_id: []\n", "CONFIG_DATA_INVALID"),
    ],
    ids=["missing", "null", "empty", "blank", "integer", "list"],
)
def test_characters_current_id_frozen_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    content: str,
    code: str,
) -> None:
    root = _fresh_root(tmp_path)
    (root / "config" / "characters.yaml").write_text(content, encoding="utf-8")
    if code == "CORE_CONFIG_SETUP_REQUIRED":
        result, _opened = _read_with_guards(root, monkeypatch)
        assert result.current_character_id is None
        assert result.provider_selection is not None
        assert result.config_problem is None
        return
    _assert_problem(root, monkeypatch, code)


def test_valid_config_returns_exact_client_settings_without_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _fresh_root(tmp_path)

    result, opened = _read_with_guards(root, monkeypatch)

    assert result.config_problem is None
    assert result.current_character_id == "sakura"
    assert result.provider_selection is not None
    assert isinstance(result.provider_selection.api_settings, ApiSettings)
    assert result.provider_selection.api_settings == ApiSettings(
        base_url="https://fixture.invalid/v1",
        api_key="REDACTED_FIXTURE_API_KEY",
        model="fixture-model",
    )
    assert [path for path, _mode, _encoding in opened] == [
        "config/system_config.yaml",
        "config/api.yaml",
        "config/characters.yaml",
    ]
    assert SUPPORTED_CORE_CONFIG_VERSION == 1


def test_valid_config_never_calls_dns_socket_or_http(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _fresh_root(tmp_path)
    fail = _fail_write("network access")
    monkeypatch.setattr(socket, "getaddrinfo", fail)
    monkeypatch.setattr(socket, "socket", fail)
    monkeypatch.setattr(socket, "create_connection", fail)
    monkeypatch.setattr(urllib.request, "urlopen", fail)
    monkeypatch.setattr(http.client.HTTPConnection, "connect", fail)
    monkeypatch.setattr(http.client.HTTPSConnection, "connect", fail)

    result, _opened = _read_with_guards(root, monkeypatch)

    assert result.config_problem is None
    assert result.provider_selection is not None


def test_provider_selection_preserves_resolver_settings_object_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _fresh_root(tmp_path)
    resolved_settings = ApiSettings(
        base_url="https://identity.invalid/v1",
        api_key="REDACTED_IDENTITY_KEY",
        model="identity-model",
    )

    def fake_resolve(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(settings=resolved_settings)

    monkeypatch.setattr(reader_module, "resolve_model_slot", fake_resolve)
    result, _opened = _read_with_guards(root, monkeypatch)

    assert result.provider_selection is not None
    assert isinstance(result.provider_selection.api_settings, ApiSettings)
    assert result.provider_selection.api_settings is resolved_settings
    assert "REDACTED_IDENTITY_KEY" not in repr(result.provider_selection)
    assert "REDACTED_IDENTITY_KEY" not in repr(result)


def test_legacy_jsonl_is_not_opened_or_repaired_by_clean_v2_reader(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _fresh_root(tmp_path)
    history = root / "data/chat_history/sakura.jsonl"
    history.parent.mkdir(parents=True, exist_ok=True)
    history.write_bytes(b'{"created_at":"2000","role":"user","content":')
    before = history.read_bytes()

    result, opened = _read_with_guards(root, monkeypatch)

    assert result.config_problem is None
    assert result.current_character_id == "sakura"
    assert all("chat_history" not in path for path, _mode, _encoding in opened)
    assert history.read_bytes() == before
    assert not list(history.parent.glob("sakura.jsonl.corrupt-*.bak"))
