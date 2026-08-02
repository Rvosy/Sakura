from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from app.core.instance import InstanceAcquireStatus, SingleInstanceGuard
from app.core_host.real_chat import RealChatBoundary
from app.storage.chat_history import ChatHistoryCompatibilityError, ChatHistoryStore


REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_DATASET = REPO_ROOT / "tests/fixtures/runtime_v2/wp_0_02/dataset"
DIRECTORY_ENV = "SAKURA_WP_3_06_ACCEPTANCE_DIRECTORY"
MODE_ENV = "SAKURA_WP_3_06_ACCEPTANCE_MODE"


def _acceptance_root() -> tuple[Path, Path]:
    directory = Path(tempfile.mkdtemp(prefix="sakura-wp-3-06-"))
    app_root = directory / "app-root"
    shutil.copytree(SOURCE_DATASET, app_root)
    (directory / ".sakura-wp-3-06-sanitized").write_text("sanitized", encoding="utf-8")
    return directory, app_root


def _manifest(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _legacy(directory: Path, mode: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env[DIRECTORY_ENV] = str(directory)
    env[MODE_ENV] = mode
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, "legacy_qt_main.py"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )


def test_real_legacy_entry_writes_and_reads_only_compatible_history() -> None:
    directory, app_root = _acceptance_root()
    try:
        before = _manifest(app_root)
        written = _legacy(directory, "legacy-write")
        assert written.returncode == 0, written.stderr
        assert (directory / "legacy.write_complete").is_file()

        history = ChatHistoryStore(app_root / "data/chat_history/fixture.jsonl")
        history.assert_compatible_append()
        history.append("user", "[WP-3-06-TAURI-USER]")
        history.append("assistant", "[WP-3-06-TAURI-REPLY]")

        read = _legacy(directory, "legacy-read")
        assert read.returncode == 0, read.stderr
        assert (directory / "legacy.read_complete").is_file()
        after = _manifest(app_root)
        assert {
            name for name in set(before) | set(after) if before.get(name) != after.get(name)
        } == {"data/chat_history/fixture.jsonl"}
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def test_future_schema_fails_before_any_legacy_acceptance_write() -> None:
    directory, app_root = _acceptance_root()
    try:
        path = app_root / "data/config/system_config.yaml"
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        document["config_version"] = 400
        path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
        before = _manifest(app_root)

        completed = _legacy(directory, "legacy-write")

        assert completed.returncode != 0
        assert not (directory / "legacy.write_complete").exists()
        assert _manifest(app_root) == before
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def test_real_legacy_entry_reports_production_lock_conflict_without_data_access() -> None:
    directory, app_root = _acceptance_root()
    guard = SingleInstanceGuard()
    try:
        assert guard.acquire() is InstanceAcquireStatus.ACQUIRED
        before = _manifest(app_root)

        completed = _legacy(directory, "legacy-read")

        assert completed.returncode == 0, completed.stderr
        assert (directory / "legacy.lock_conflict").read_text(encoding="utf-8") == "already_running"
        assert _manifest(app_root) == before
    finally:
        guard.release()
        shutil.rmtree(directory, ignore_errors=True)


def test_current_history_accepts_compatible_append_and_preserves_other_data() -> None:
    directory, app_root = _acceptance_root()
    try:
        path = app_root / "data/chat_history/fixture.jsonl"
        before = _manifest(app_root)
        store = ChatHistoryStore(path, "Fixture Character")

        store.assert_compatible_append()
        store.append("user", "[WP-3-06-INTEGRATION]")
        store.assert_compatible_append()

        after = _manifest(app_root)
        assert {
            name for name in set(before) | set(after) if before.get(name) != after.get(name)
        } == {"data/chat_history/fixture.jsonl"}
        assert store.load_recent(1)[0].content == "[WP-3-06-INTEGRATION]"
    finally:
        shutil.rmtree(directory, ignore_errors=True)


@pytest.mark.parametrize(
    "payload",
    [
        b'{"created_at":"2000","role":"user","content":"ok"}\n{"content":',
        b'{"created_at":"2000","role":"user","content":7}\n',
        b'not-json\n',
        b'\xff\n',
    ],
)
def test_corrupt_history_blocks_compatible_append_without_repair(payload: bytes) -> None:
    directory, app_root = _acceptance_root()
    try:
        path = app_root / "data/chat_history/fixture.jsonl"
        path.write_bytes(payload)
        before = _manifest(app_root)

        with pytest.raises(ChatHistoryCompatibilityError):
            ChatHistoryStore(path).assert_compatible_append()

        assert _manifest(app_root) == before
        assert not list(path.parent.glob("fixture.jsonl.corrupt-*.bak"))
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def test_history_symlink_is_not_a_compatible_write_target() -> None:
    directory, app_root = _acceptance_root()
    try:
        path = app_root / "data/chat_history/fixture.jsonl"
        outside = directory / "outside.jsonl"
        outside.write_text(
            json.dumps({"created_at": "2000", "role": "user", "content": "outside"})
            + "\n",
            encoding="utf-8",
        )
        path.unlink()
        try:
            path.symlink_to(outside)
        except OSError:
            pytest.skip("symlinks are unavailable in this Windows test environment")
        before = outside.read_bytes()

        with pytest.raises(ChatHistoryCompatibilityError):
            ChatHistoryStore(path).assert_compatible_append()

        assert outside.read_bytes() == before
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def test_history_symlinked_ancestor_is_not_a_compatible_write_target() -> None:
    directory, app_root = _acceptance_root()
    try:
        real_root = directory / "outside-root"
        real_history = real_root / "chat_history"
        real_history.mkdir(parents=True)
        target = real_history / "fixture.jsonl"
        target.write_text(
            json.dumps({"created_at": "2000", "role": "user", "content": "outside"})
            + "\n",
            encoding="utf-8",
        )
        linked_root = app_root / "linked-data"
        try:
            linked_root.symlink_to(real_root, target_is_directory=True)
        except OSError:
            pytest.skip("symlinks are unavailable in this Windows test environment")
        aliased_target = linked_root / "chat_history/fixture.jsonl"
        before = target.read_bytes()

        with pytest.raises(ChatHistoryCompatibilityError):
            ChatHistoryStore(aliased_target).assert_compatible_append()

        assert target.read_bytes() == before
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def test_real_chat_fails_closed_before_provider_or_history_write() -> None:
    directory, app_root = _acceptance_root()
    try:
        path = app_root / "data/chat_history/fixture.jsonl"
        path.write_bytes(b'{"created_at":"2000","role":"user","content":')
        before = _manifest(app_root)
        calls: list[object] = []
        events: list[dict[str, object]] = []

        class Pipeline:
            def run_user_message(self, *_args: object, **_kwargs: object) -> object:
                calls.append("provider")
                raise AssertionError("provider must not run for incompatible history")

        session = SimpleNamespace(
            character=SimpleNamespace(id="fixture", display_name="Fixture Character"),
            pipeline=Pipeline(),
        )
        generation = "00000000-0000-4000-8000-000000003006"
        credential = "6" * 32
        request = {
            "protocolMajor": 2,
            "protocolMinor": 2,
            "kind": "request",
            "generationId": generation,
            "generationCredential": credential,
            "id": "wp-3-06-corrupt-history",
            "name": "chat.send",
            "payload": {
                "operationId": "wp-3-06-corrupt-history",
                "message": "must not append",
            },
        }
        boundary = RealChatBoundary(
            generation,
            credential,
            app_root,
            session_provider=lambda: session,
            event_publisher=events.append,
        )

        boundary.reserve_send(request)
        response = boundary.handle_send(request)

        assert response["ok"] is True
        assert calls == []
        assert [event["name"] for event in events] == ["chat.started", "chat.failed"]
        assert events[-1]["payload"]["error"] == {
            "code": "HISTORY_COMPATIBILITY_READ_ONLY",
            "message": "Chat history is read-only because existing data is incompatible",
            "retryable": False,
            "details": {},
        }
        assert _manifest(app_root) == before
    finally:
        shutil.rmtree(directory, ignore_errors=True)
