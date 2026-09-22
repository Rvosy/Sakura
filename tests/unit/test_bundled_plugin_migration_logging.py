from __future__ import annotations

import io
import json
import shutil
import sys
from pathlib import Path

import pytest

from app.core_host.runtime_logging import CORE_BRIDGE_PREFIX, RuntimeLoggingBridge
from app.plugins import bundled_migrations as migration
from app.storage.runtime_roots import RuntimeRoots


PLUGIN = "sakura_mobile"


@pytest.fixture
def roots(tmp_path, monkeypatch):
    monkeypatch.setattr(migration, "MIGRATIONS", {PLUGIN: PLUGIN})
    roots = RuntimeRoots(tmp_path / "distribution", tmp_path / "user")
    (roots.user_root / "config").mkdir(parents=True)
    plugin = roots.distribution_root / migration.PAYLOAD_PATH / "plugins" / PLUGIN
    plugin.mkdir(parents=True)
    (plugin / "plugin.yaml").write_text(
        "api: 4\nid: sakura_mobile\nname: Mobile\nversion: 1.0.0\nentry: plugin:Plugin\n"
    )
    (plugin / "plugin.py").write_text("class Plugin: pass\n")
    return roots


def run_with_logs(roots):
    stream = io.BytesIO()
    bridge = RuntimeLoggingBridge(stream)
    bridge.install()
    try:
        failures = migration.migrate_bundled_plugins(roots)
    finally:
        assert bridge.close()
    records = [json.loads(line.removeprefix(CORE_BRIDGE_PREFIX))
               for line in stream.getvalue().splitlines() if line.startswith(CORE_BRIDGE_PREFIX)]
    return failures, records


def event(records, name):
    return next(record for record in records if record["event"] == f"plugin.migration.{name}")


def test_migration_source_result_and_one_time_revalidation_survive_log_bridge(roots):
    failures, records = run_with_logs(roots)
    assert failures == {}
    assert event(records, "started")["severity"] == "info"
    source = event(records, "source_selected")
    assert source["plugin_id"] == PLUGIN
    assert source["attributes"]["code_source"] == "payload"
    assert source["attributes"]["dependency_source"] == "none"
    assert source["attributes"]["version"] == "1.0.0"
    result = event(records, "plugin_completed")["attributes"]
    assert result["outcome"] == "migrated"
    assert result["elapsed_ms"] >= 0
    assert event(records, "completed")["attributes"]["count"] == 1

    state = roots.user_root / "config" / migration.STATE_NAME
    state.write_text(json.dumps({PLUGIN: "completed"}))
    failures, records = run_with_logs(roots)
    assert failures == {}
    assert event(records, "plugin_completed")["attributes"]["outcome"] == "revalidated"
    assert not any(record["event"] == "plugin.migration.source_selected" for record in records)
    assert run_with_logs(roots) == ({}, [])


def test_kept_existing_install_is_distinguished_from_copying(roots):
    source = roots.distribution_root / migration.PAYLOAD_PATH / "plugins" / PLUGIN
    shutil.copytree(source, roots.user_root / "plugins/user" / PLUGIN)
    failures, records = run_with_logs(roots)
    assert failures == {}
    assert event(records, "plugin_completed")["attributes"]["outcome"] == "retained"
    assert not any(record["event"] == "plugin.migration.source_selected" for record in records)


def test_selected_dependency_source_survives_earlier_rejected_candidates(roots):
    payload = roots.distribution_root / migration.PAYLOAD_PATH
    source = payload / "plugins" / PLUGIN
    (source / "requirements.txt").write_text("migration-probe==1.0\n")
    (source / "plugin.py").write_text("import migration_probe\nclass Plugin: pass\n")
    dependencies = payload / "dependencies" / PLUGIN
    dependencies.mkdir(parents=True)
    (dependencies / "migration_probe.py").write_text("value = 1\n")
    (dependencies / ".sakura-dependencies.json").write_text(json.dumps({
        "schemaVersion": 1, "kind": "requirements.txt", "python": f"{sys.version_info.major}.{sys.version_info.minor}",
    }))
    failures, records = run_with_logs(roots)
    assert failures == {}
    assert event(records, "candidate_rejected")["severity"] == "info"
    selected = event(records, "source_selected")["attributes"]
    assert selected["code_source"] == "payload"
    assert selected["dependency_source"] == "payload"
    assert event(records, "plugin_completed")["attributes"]["dependency_source"] == "payload"


def test_rejected_candidate_is_local_info_and_copy_failure_keeps_original_diagnostics(roots, monkeypatch):
    old = roots.distribution_root / "plugins/builtin" / PLUGIN
    old.mkdir(parents=True)
    (old / "plugin.yaml").write_text(
        "api: 4\nid: sakura_mobile\nversion: 0.1.0\nentry: plugin:Plugin\nrequires: [sakura.host.model_slots]\n"
    )
    (old / "plugin.py").write_text("class Plugin: pass\n")

    def disk_full(source, destination, **kwargs):
        raise OSError(28, "migration staging disk is full")

    monkeypatch.setattr(migration.shutil, "copytree", disk_full)
    failures, records = run_with_logs(roots)
    assert failures == {PLUGIN: "PLUGIN_MIGRATION_FAILED"}
    assert event(records, "candidate_rejected")["severity"] == "info"
    failure = event(records, "failed")
    assert failure["severity"] == "error"
    assert failure["plugin_id"] == PLUGIN
    details = failure["attributes"]
    assert details["code"] == "PLUGIN_MIGRATION_FAILED"
    assert details["stage"] == "copy_code"
    assert details["code_source"] == "payload"
    assert details["version"] == "1.0.0"
    assert details["errno"] == 28
    assert "migration staging disk is full" in details["diagnostic"]
    assert "disk_full" in details["exception_stack"]
    summary = event(records, "completed")["attributes"]
    assert summary["outcome"] == "failed"
    assert summary["failed"] == 1
    assert summary["count"] == 0


@pytest.mark.parametrize("state", ['{"sakura_mobile":', '{"sakura_mobile": "unknown"}'])
def test_invalid_migration_state_preserves_diagnostics_and_emits_summary(roots, state):
    state_path = roots.user_root / "config" / migration.STATE_NAME
    state_path.write_text(state)
    failures, records = run_with_logs(roots)
    assert failures[PLUGIN] == "PLUGIN_MIGRATION_STATE_INVALID"
    assert event(records, "failed")["attributes"]["stage"] == "read_state"
    assert event(records, "completed")["attributes"]["outcome"] == "failed"
    assert state_path.read_text() == state


def test_publication_failure_records_rollback_and_original_stage(roots, monkeypatch):
    replace = migration.os.replace

    def fail_publish(source, destination):
        if Path(source).name == "code":
            raise PermissionError("migration code replacement was denied")
        return replace(source, destination)

    monkeypatch.setattr(migration.os, "replace", fail_publish)
    failures, records = run_with_logs(roots)
    assert failures == {PLUGIN: "PLUGIN_MIGRATION_FAILED"}
    assert event(records, "rollback_completed")["attributes"]["outcome"] == "restored"
    failure = event(records, "failed")["attributes"]
    assert failure["stage"] == "publish_code"
    assert "migration code replacement was denied" in failure["diagnostic"]


def test_real_entry_failure_keeps_subprocess_trace_exit_code_and_redacts_secrets(roots):
    source = roots.distribution_root / migration.PAYLOAD_PATH / "plugins" / PLUGIN
    (source / "plugin.py").write_text(
        "import sys\n"
        "print('token=migration-test-private-token', file=sys.stderr)\n"
        "print('preparing dependency ' * 150, file=sys.stderr)\n"
        "import sakura_missing_migration_dependency\n"
        "class Plugin: pass\n"
    )
    failures, records = run_with_logs(roots)
    assert failures == {PLUGIN: "PLUGIN_MIGRATION_FAILED"}
    failure = event(records, "failed")["attributes"]
    assert failure["code"] == "PLUGIN_MIGRATION_FAILED"
    assert failure["cause_code"] == "PLUGIN_ENTRY_IMPORT_FAILED"
    assert failure["stage"] == "validate_entry"
    assert "exit_code=1" in failure["diagnostic"]
    assert "ModuleNotFoundError" in failure["diagnostic"]
    assert "sakura_missing_migration_dependency" in failure["diagnostic"]
    assert "ModuleNotFoundError" in failure["exception_stack"]
    assert "_validate_entry" in failure["exception_stack"]
    serialized = json.dumps(records)
    assert "migration-test-private-token" not in serialized
    assert "[REDACTED]" in serialized
