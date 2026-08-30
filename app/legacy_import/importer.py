from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import uuid
from collections import deque
from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from contextvars import ContextVar
from contextlib import closing
from pathlib import Path, PureWindowsPath

import yaml

from app.agent.builtin_tools import TodoStore
from app.agent.desktop_tools import NotesStore
from app.agent.mcp.config import load_mcp_config
from app.agent.reminders import ReminderStore
from app.config.character_loader import CharacterRegistry
from app.config.character_studio import CharacterStudioDoc, CharacterStudioService
from app.config.core_config_reader import CoreConfigReader
from app.config.settings_service import AppSettingsService
from app.plugins.inventory import PluginDesiredStateStore
from app.storage.timeline import TimelineStore, _encode_cursor

from .configuration import add_character_extensions, migrate_configuration
from .errors import LegacyImportError
from .files import (
    copy_file_checked,
    copy_tree_checked,
    copy_tree_fast_checked,
    is_link_or_junction,
    sha256_file,
    tree_stats,
)
from .history import HistoryImportStats, import_history
from .inspector import (
    detect_legacy_source_platform,
    inspect_installation,
    legacy_tts_root,
)
from .models import ImportReport, LegacyInspection
from .transaction import PendingCommit, commit_payload, finalize_commit, rollback_commit


CancelChecker = Callable[[], bool]
Progress = Callable[[str, int, str], None]
Diagnostic = Callable[[str, str, Mapping[str, object], str], None]
_NO_CANCEL = lambda: False
_NO_PROGRESS = lambda _stage, _percent, _message: None
_NO_DIAGNOSTIC = lambda _event, _message, _attributes, _severity: None
_DIAGNOSTIC_SINK: ContextVar[Diagnostic] = ContextVar(
    "sakura_legacy_import_diagnostic_sink",
    default=_NO_DIAGNOSTIC,
)
_TTS_PROFILE_NAMES = (
    "tts_infer.yaml",
    "tts_infer_sakura_managed.yaml",
    "tts_infer_sakura_macos.yaml",
)
_TTS_PROFILE_PATH_FIELDS = (
    "bert_base_path",
    "cnhuhbert_base_path",
    "t2s_weights_path",
    "vits_weights_path",
)
_OPTIONAL_TTS_INSPECTION_CODES = {
    "LEGACY_TTS_LINK_BROKEN",
    "LEGACY_TTS_LAYOUT_UNRECOGNIZED",
    "LEGACY_TTS_TARGET_OVERLAP",
}


def inspect_legacy_installation(source: Path, target: Path) -> LegacyInspection:
    return inspect_installation(source, target)


def run_legacy_import(
    source: Path,
    target: Path,
    *,
    import_id: str | None = None,
    cancelled: CancelChecker = _NO_CANCEL,
    progress: Progress = _NO_PROGRESS,
    diagnostic: Diagnostic = _NO_DIAGNOSTIC,
    finalize: bool = False,
    inspection: LegacyInspection | None = None,
) -> tuple[ImportReport, PendingCommit | None]:
    source = Path(source).resolve(strict=True)
    target = Path(target).resolve(strict=True)
    inspection = inspection or inspect_installation(source, target)
    if not inspection.compatible:
        first = inspection.blockers[0]
        raise LegacyImportError(str(first["code"]), "inspect")
    import_id = import_id or uuid.uuid4().hex
    if not re.fullmatch(r"[A-Za-z0-9-]{8,64}", import_id):
        raise LegacyImportError("LEGACY_IMPORT_ID_INVALID", "inspect")
    staging = target / f".legacy-import-staging-{import_id}"
    payload = staging / "payload"
    cancel_path = target / f".legacy-import-cancel-{import_id}"

    def is_cancelled() -> bool:
        return cancelled() or cancel_path.exists()

    if staging.exists():
        raise LegacyImportError("LEGACY_IMPORT_RECOVERY_REQUIRED", "staging")
    payload.mkdir(parents=True)
    report = ImportReport(import_id=import_id, detected_version=inspection.detected_version)
    report.warnings.extend(inspection.warnings)
    pending: PendingCommit | None = None
    diagnostic_token = _DIAGNOSTIC_SINK.set(diagnostic)
    _log_legacy_import(
        import_id,
        "legacy_import.started",
        "旧版本迁移开始",
        {"detected_version": inspection.detected_version},
    )
    try:
        # Timeline and Memory are the only irreplaceable legacy domains.  Their
        # character identity comes from the legacy scope itself; a character
        # package is useful for case normalization but is not their owner.
        discovered_character_ids = _discover_character_ids(source)
        processed_counts, _current_character = _legacy_curation(source)
        mapped_counts = _mapped_processed_counts(
            processed_counts, discovered_character_ids
        )
        progress("staging", 5, "正在转换角色对话历史")
        history = import_history(
            source,
            payload,
            character_ids=discovered_character_ids,
            processed_counts=mapped_counts,
        )
        report.counts.update(
            {
                "historyRecords": history.source_records,
                "timelineEntries": history.timeline_entries,
                "historyErrorsQuarantined": history.errors_quarantined,
            }
        )

        progress("staging", 20, "正在迁移角色长期记忆")
        memory_files, memory_bytes = _copy_memory(
            source,
            payload,
            is_cancelled,
            import_id=import_id,
        )
        report.counts["memoryFiles"] = memory_files
        report.bytes["memory"] = memory_bytes
        _validate_memory(payload / "data" / "memory")
        if memory_files:
            model_files, model_bytes = _prepare_memory_model(
                source,
                target,
                payload,
                is_cancelled,
                progress=progress,
                import_id=import_id,
            )
            report.counts["memoryModelFiles"] = model_files
            report.bytes["memoryModel"] = model_bytes
        _log_legacy_import(
            import_id,
            "legacy_import.memory_completed",
            "旧版本长期记忆迁移完成",
            {
                "files": memory_files,
                "bytes": memory_bytes,
                "model_files": report.counts.get("memoryModelFiles", 0),
                "model_bytes": report.bytes.get("memoryModel", 0),
            },
        )
        _write_curation_states(payload, mapped_counts, history)

        progress("staging", 55, "正在迁移配置")
        report.counts.update(
            migrate_configuration(source, payload, new_tts_root=target / "tts")
        )
        _validate_optional_tts_configuration(
            payload,
            report,
            import_id=import_id,
        )
        _check_cancelled(is_cancelled)

        progress("staging", 60, "正在迁移其他用户数据")
        _copy_other_user_data(source, payload, is_cancelled, report)

        # Validate irreplaceable data and current configuration before trying
        # replaceable resource domains.  A character/TTS failure below becomes
        # a report warning and must not roll this payload back.
        progress("validating", 65, "正在校验核心迁移数据")
        _validate_staged(payload, import_id=import_id)
        _check_cancelled(is_cancelled, stage="validating")

        progress("staging", 68, "正在尝试迁移角色包")
        character_ids = _copy_characters_optional(
            source,
            payload,
            is_cancelled,
            import_id=import_id,
            report=report,
        )

        progress("staging", 72, "正在尝试迁移 TTS 资源")
        _copy_tts_optional(
            source,
            payload,
            is_cancelled,
            inspection=inspection,
            character_ids=character_ids,
            import_id=import_id,
            progress=progress,
            report=report,
        )

        progress("validating", 90, "正在生成迁移校验清单")
        last_manifest_percent = -1

        def manifest_progress(completed_bytes: int, expected_bytes: int) -> None:
            nonlocal last_manifest_percent
            ratio = (
                min(1.0, max(0.0, completed_bytes / expected_bytes))
                if expected_bytes > 0
                else 1.0
            )
            manifest_percent = int(ratio * 100)
            overall_percent = min(94, 90 + int(ratio * 4))
            if overall_percent == last_manifest_percent:
                return
            last_manifest_percent = overall_percent
            progress(
                "validating",
                overall_percent,
                f"正在校验迁移文件（{manifest_percent}%）",
            )

        report.artifacts = _build_artifact_manifest(
            payload,
            is_cancelled,
            byte_progress=manifest_progress,
        )
        _write_report(payload, report)

        _check_cancelled(is_cancelled, stage="validating")

        progress("committing", 95, "正在提交迁移数据")
        pending = commit_payload(target, import_id, payload)
        if finalize:
            finalize_commit(pending)
            pending = None
        progress("core_validating", 98, "等待 Sakura Core 校验")
        _log_legacy_import(
            import_id,
            "legacy_import.staged",
            "旧版本迁移已提交，等待 Core 校验",
        )
        return report, pending
    except Exception as exc:
        _log_legacy_import(
            import_id,
            "legacy_import.failed",
            "旧版本迁移失败",
            _exception_log_attributes(exc, stage=str(getattr(exc, "stage", "transaction"))),
            severity="error",
        )
        if pending is not None and pending.journal_path.exists():
            rollback_commit(pending)
        else:
            shutil.rmtree(staging, ignore_errors=True)
        raise
    finally:
        cancel_path.unlink(missing_ok=True)
        _DIAGNOSTIC_SINK.reset(diagnostic_token)


def _discover_character_ids(source: Path) -> tuple[str, ...]:
    """Read stable IDs for Timeline/Memory without making packages mandatory."""

    root = source / "characters"
    if not root.is_dir():
        return ()
    ids: list[str] = []
    for manifest in sorted(root.glob("*/character.json")):
        try:
            value = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        character_id = value.get("id") if isinstance(value, dict) else None
        if isinstance(character_id, str) and character_id.strip():
            ids.append(character_id.strip())
    return tuple(ids)


def _copy_characters_optional(
    source: Path,
    payload: Path,
    cancelled: CancelChecker,
    *,
    import_id: str,
    report: ImportReport,
) -> tuple[str, ...]:
    selection = payload / "config" / "characters.yaml"
    selection_before = selection.read_bytes() if selection.is_file() else None
    try:
        character_files, character_bytes = copy_tree_checked(
            source / "characters",
            payload / "characters",
            cancelled=cancelled,
        )
        character_ids = add_character_extensions(payload)
        _validate_characters(
            payload,
            import_id=import_id,
            failure_severity="warning",
        )
    except Exception as exc:
        if _must_abort_optional_domain(exc, cancelled):
            raise
        _discard_optional_domain_staging(payload / "characters")
        if selection_before is not None:
            selection.write_bytes(selection_before)
        _record_optional_domain_skipped(
            report,
            import_id=import_id,
            domain="characters",
            code="LEGACY_CHARACTER_IMPORT_SKIPPED",
            exc=exc,
        )
        report.counts.update(
            {"characters": 0, "characterFiles": 0, "charactersSkipped": 1}
        )
        report.bytes["characters"] = 0
        return ()

    report.counts["characters"] = len(character_ids)
    report.counts["characterFiles"] = character_files
    report.bytes["characters"] = character_bytes
    return character_ids


def _copy_tts_optional(
    source: Path,
    payload: Path,
    cancelled: CancelChecker,
    *,
    inspection: LegacyInspection,
    character_ids: tuple[str, ...],
    import_id: str,
    progress: Progress,
    report: ImportReport,
) -> None:
    inspection_issue = next(
        (
            str(item.get("code"))
            for item in inspection.warnings
            if item.get("code") in _OPTIONAL_TTS_INSPECTION_CODES
        ),
        None,
    )
    if inspection_issue is not None:
        _record_optional_domain_skipped(
            report,
            import_id=import_id,
            domain="tts",
            code="LEGACY_TTS_IMPORT_SKIPPED",
            reason_code=inspection_issue,
        )
        report.counts.update({"ttsFiles": 0, "ttsSkipped": 1})
        report.bytes["tts"] = 0
        return


    try:
        tts_files, tts_bytes = _copy_tts(
            source,
            payload,
            cancelled,
            import_id=import_id,
            progress=progress,
            warnings=report.warnings,
            match_characters=False,
            failure_severity="warning",
        )
    except Exception as exc:
        if _must_abort_optional_domain(exc, cancelled):
            raise
        _discard_optional_domain_staging(payload / "tts")
        _record_optional_domain_skipped(
            report,
            import_id=import_id,
            domain="tts",
            code="LEGACY_TTS_IMPORT_SKIPPED",
            exc=exc,
        )
        report.counts.update({"ttsFiles": 0, "ttsSkipped": 1})
        report.bytes["tts"] = 0
        return

    report.counts["ttsFiles"] = tts_files
    report.bytes["tts"] = tts_bytes
    if character_ids:
        try:
            _attach_legacy_onnx_to_characters(payload, character_ids)
            add_character_extensions(payload)
        except Exception as exc:
            if _must_abort_optional_domain(exc, cancelled):
                raise
            _record_optional_domain_skipped(
                report,
                import_id=import_id,
                domain="tts_onnx_binding",
                code="LEGACY_TTS_ONNX_BINDING_SKIPPED",
                exc=exc,
            )


def _attach_legacy_onnx_to_characters(
    payload: Path, character_ids: tuple[str, ...]
) -> None:
    orphan_root = payload / "tts" / "onnx"
    characters_root = payload / "characters"
    if not orphan_root.is_dir() or not characters_root.is_dir():
        return
    character_dirs = {
        child.name.casefold(): child for child in characters_root.iterdir() if child.is_dir()
    }
    for character_id in character_ids:
        character_dir = character_dirs.get(character_id.casefold())
        if character_dir is None:
            continue
        exact = orphan_root / character_id
        candidates = (
            [exact]
            if exact.is_dir()
            else [
                child
                for child in orphan_root.iterdir()
                if child.is_dir() and child.name.casefold() == character_id.casefold()
            ]
        )
        if len(candidates) != 1:
            continue
        destination = character_dir / "voice" / "onnx"
        if destination.exists():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(candidates[0], destination)


def _validate_optional_tts_configuration(
    payload: Path,
    report: ImportReport,
    *,
    import_id: str,
) -> None:
    try:
        _validate_tts_configs(payload)
    except Exception as exc:
        for plugin_id in ("sakura.tts.gpt-sovits", "sakura.tts.genie"):
            config = payload / "data" / "plugins" / plugin_id / "config.json"
            config.unlink(missing_ok=True)
        _record_optional_domain_skipped(
            report,
            import_id=import_id,
            domain="tts_config",
            code="LEGACY_TTS_CONFIG_SKIPPED",
            exc=exc,
        )
        report.counts["ttsConfig"] = 0
        report.counts["ttsConfigSkipped"] = 1


def _must_abort_optional_domain(exc: Exception, cancelled: CancelChecker) -> bool:
    return cancelled() or (
        isinstance(exc, LegacyImportError) and exc.code == "LEGACY_IMPORT_CANCELLED"
    )


def _discard_optional_domain_staging(path: Path) -> None:
    if not os.path.lexists(path):
        return
    try:
        shutil.rmtree(path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise LegacyImportError(
            "LEGACY_OPTIONAL_DOMAIN_CLEANUP_FAILED", "staging", path.name
        ) from exc
    if os.path.lexists(path):
        raise LegacyImportError(
            "LEGACY_OPTIONAL_DOMAIN_CLEANUP_FAILED", "staging", path.name
        )


def _record_optional_domain_skipped(
    report: ImportReport,
    *,
    import_id: str,
    domain: str,
    code: str,
    exc: Exception | None = None,
    reason_code: str | None = None,
) -> None:
    exception_attributes = (
        _exception_log_attributes(exc, stage=domain) if exc is not None else {}
    )
    stable_reason = reason_code or str(
        exception_attributes.get("reason_code", "LEGACY_IMPORT_FAILED")
    )
    report.warnings.append(
        {"code": code, "stage": domain, "reasonCode": stable_reason}
    )
    attributes: dict[str, object] = {
        "code": code,
        "reason_code": stable_reason,
        "stage": domain,
    }
    if exc is not None:
        attributes.update(exception_attributes)
        attributes["code"] = code
        attributes["reason_code"] = stable_reason
    _log_legacy_import(
        import_id,
        f"legacy_import.{domain}_skipped",
        "可恢复迁移域已跳过",
        attributes,
        severity="warning",
    )


class _CancellationEventAdapter:
    """Expose the importer's live cancellation check as a threading.Event API."""

    def __init__(self, cancelled: CancelChecker) -> None:
        self._cancelled = cancelled

    def is_set(self) -> bool:
        return self._cancelled()


def _prepare_memory_model(
    source: Path,
    target: Path,
    payload: Path,
    cancelled: CancelChecker,
    *,
    progress: Progress,
    import_id: str,
) -> tuple[int, int]:
    """Stage a verified v2 ONNX model whenever legacy memory is imported.

    A complete target cache is retained.  A compatible cache embedded in the
    source is copied into the transaction; old Hugging Face/PyTorch caches do
    not match the fixed FastEmbed revision and therefore fall through to the
    current downloader.
    """

    from plugins.builtin.sakura_mem0.memory import (
        DEFAULT_EMBEDDING_MODEL,
        DEFAULT_EMBEDDING_MODEL_CACHE_NAME,
        MemoryModelTaskCancelled,
        _embedding_model_cached,
        _embedding_model_snapshot,
        _validate_fastembed_snapshot_artifacts,
        download_embedding_model,
    )

    target_cache = target / "data" / "cache" / "memory"
    staged_cache = payload / "data" / "cache" / "memory"

    def verified_snapshot(cache: Path) -> Path | None:
        if not _embedding_model_cached(
            DEFAULT_EMBEDDING_MODEL,
            cache_dir=cache,
        ):
            return None
        snapshot = _embedding_model_snapshot(
            DEFAULT_EMBEDDING_MODEL,
            cache_dir=cache,
        )
        if snapshot is None:
            return None
        _validate_fastembed_snapshot_artifacts(snapshot)
        return snapshot

    try:
        _check_cancelled(cancelled)
        target_snapshot = verified_snapshot(target_cache)
        if target_snapshot is not None:
            files, size = tree_stats(target_snapshot.parents[1])
            progress("staging", 54, "记忆模型已就绪")
            _log_legacy_import(
                import_id,
                "legacy_import.memory_model_reused",
                "目标中的记忆模型已通过校验",
                {"files": files, "bytes": size},
            )
            return files, size

        source_caches = (
            source / "data" / "cache" / "memory",
            source / "data" / "fastembed-cache",
        )
        for source_cache in source_caches:
            source_snapshot = verified_snapshot(source_cache)
            if source_snapshot is None:
                continue
            source_model = source_snapshot.parents[1]
            staged_model = staged_cache / DEFAULT_EMBEDDING_MODEL_CACHE_NAME
            progress("staging", 46, "正在迁移记忆模型")
            copy_tree_checked(source_model, staged_model, cancelled=cancelled)
            staged_snapshot = verified_snapshot(staged_cache)
            if staged_snapshot is None:
                raise RuntimeError("staged memory model is incomplete")
            files, size = tree_stats(staged_model)
            progress("staging", 54, "记忆模型已就绪")
            _log_legacy_import(
                import_id,
                "legacy_import.memory_model_copied",
                "随旧版本迁移的记忆模型已通过校验",
                {"files": files, "bytes": size},
            )
            return files, size

        progress("staging", 46, "正在准备记忆模型")
        last_percent = -1

        def model_progress(_stage: str, percent: int) -> None:
            nonlocal last_percent
            _check_cancelled(cancelled)
            bounded = min(100, max(0, int(percent)))
            overall = min(54, 46 + int(bounded * 8 / 100))
            if overall == last_percent:
                return
            last_percent = overall
            progress("staging", overall, f"正在准备记忆模型（{bounded}%）")

        result = download_embedding_model(
            cache_dir=staged_cache,
            progress=model_progress,
            cancel=_CancellationEventAdapter(cancelled),
        )
        staged_snapshot = verified_snapshot(staged_cache)
        if staged_snapshot is None:
            raise RuntimeError("prepared memory model is incomplete")
        files, size = tree_stats(result.model_dir)
        progress("staging", 54, "记忆模型已就绪")
        _log_legacy_import(
            import_id,
            "legacy_import.memory_model_prepared",
            "当前记忆模型已写入迁移事务并通过校验",
            {"files": files, "bytes": size},
        )
        return files, size
    except LegacyImportError:
        raise
    except MemoryModelTaskCancelled:
        raise LegacyImportError("LEGACY_IMPORT_CANCELLED", "staging") from None
    except Exception as exc:  # noqa: BLE001 - only stable diagnostics cross the boundary
        _log_legacy_import(
            import_id,
            "legacy_import.memory_model_failed",
            "迁移所需的记忆模型准备失败",
            _exception_log_attributes(exc, stage="memory_model_prepare"),
            severity="error",
        )
        raise LegacyImportError(
            "LEGACY_MEMORY_MODEL_PREPARATION_FAILED",
            "staging",
        ) from None


def _copy_tts(
    source: Path,
    payload: Path,
    cancelled: CancelChecker,
    *,
    import_id: str = "direct-check",
    progress: Progress = _NO_PROGRESS,
    warnings: list[dict[str, object]] | None = None,
    match_characters: bool = True,
    failure_severity: str = "error",
) -> tuple[int, int]:
    files = total = 0
    tts = legacy_tts_root(source)
    skipped_absolute_links = 0

    def skipped_absolute_link() -> None:
        nonlocal skipped_absolute_links
        skipped_absolute_links += 1

    if os.path.lexists(tts):
        actual = tts.resolve(strict=True) if is_link_or_junction(tts) else tts
        latest_detail: dict[str, object] = {
            "detail_stage": "preflight",
            "copy_method": "unknown",
        }

        def copy_diagnostic(event: str, attributes: Mapping[str, object]) -> None:
            latest_detail.clear()
            latest_detail.update(attributes)
            severity = failure_severity if event == "failed" else "info"
            _log_legacy_import(
                import_id,
                f"legacy_import.tts_copy_{event}",
                "TTS 资源复制诊断",
                attributes,
                severity=severity,
            )

        last_percent = -1

        def copy_byte_progress(copied_bytes: int, expected_bytes: int) -> None:
            nonlocal last_percent
            ratio = (
                min(1.0, max(0.0, copied_bytes / expected_bytes))
                if expected_bytes > 0
                else 1.0
            )
            copy_percent = int(ratio * 100)
            overall_percent = min(89, 73 + int(ratio * 16))
            if overall_percent == last_percent:
                return
            last_percent = overall_percent
            progress(
                "staging",
                overall_percent,
                f"正在复制 TTS 资源（{copy_percent}%）",
            )

        try:
            child_files, child_bytes = copy_tree_fast_checked(
                actual,
                payload / "tts",
                cancelled=cancelled,
                skip_noise=True,
                noise_names_at_root_only=True,
                diagnostic=copy_diagnostic,
                byte_progress=copy_byte_progress,
                preserve_internal_symlinks=(
                    detect_legacy_source_platform(source) == "macos"
                ),
                on_skipped_absolute_symlink=skipped_absolute_link,
            )
        except Exception as exc:
            _log_legacy_import(
                import_id,
                "legacy_import.tts_copy_failed",
                "TTS 资源复制失败",
                {**latest_detail, **_exception_log_attributes(exc, stage="tts_copy")},
                severity=failure_severity,
            )
            raise
        files += child_files
        total += child_bytes
    if skipped_absolute_links:
        warning = {
            "code": "LEGACY_TTS_ABSOLUTE_LINKS_SKIPPED",
            "stage": "staging",
            "items": skipped_absolute_links,
        }
        if warnings is not None:
            warnings.append(warning)
        _log_legacy_import(
            import_id,
            "legacy_import.tts_absolute_links_skipped",
            "旧版 TTS 绝对链接未复制",
            {
                "detail_stage": "link_adaptation",
                "links": skipped_absolute_links,
            },
            severity="warning",
        )
    _log_legacy_import(
        import_id,
        "legacy_import.tts_onnx_started",
        "开始合并旧版 TTS ONNX 资源",
        {"detail_stage": "legacy_onnx"},
    )
    try:
        child_files, child_bytes = _copy_legacy_onnx(
            source,
            payload,
            cancelled,
            match_characters=match_characters,
        )
    except Exception as exc:
        _log_legacy_import(
            import_id,
            "legacy_import.tts_copy_failed",
            "旧版 TTS ONNX 资源合并失败",
            {
                "detail_stage": "legacy_onnx",
                **_exception_log_attributes(exc, stage="legacy_onnx"),
            },
            severity=failure_severity,
        )
        raise
    files += child_files
    total += child_bytes
    adapted_profiles, byte_delta = _sanitize_tts_runtime_profiles(payload / "tts")
    total += byte_delta
    if adapted_profiles:
        _log_legacy_import(
            import_id,
            "legacy_import.tts_profiles_adapted",
            "旧版 TTS 托管配置已适配",
            {
                "detail_stage": "profile_adaptation",
                "profiles": adapted_profiles,
                "byte_delta": byte_delta,
            },
        )
    sanitized_paths, byte_delta = _sanitize_tts_runtime_pth_files(payload / "tts")
    total += byte_delta
    if sanitized_paths:
        _log_legacy_import(
            import_id,
            "legacy_import.tts_runtime_paths_sanitized",
            "旧版 TTS Python 路径已适配",
            {
                "detail_stage": "runtime_path_adaptation",
                "pth_files": sanitized_paths,
                "byte_delta": byte_delta,
            },
        )
    _log_legacy_import(
        import_id,
        "legacy_import.tts_completed",
        "旧版本 TTS 资源迁移完成",
        {"files": files, "bytes": total},
    )
    return files, total


def _sanitize_tts_runtime_profiles(tts_root: Path) -> tuple[int, int]:
    """Remove old-install absolute paths from copied managed TTS profiles."""

    if not tts_root.is_dir():
        return 0, 0
    runtime_roots = [tts_root]
    runtime_roots.extend(path for path in tts_root.iterdir() if path.is_dir())
    changed = 0
    byte_delta = 0
    for runtime_root in runtime_roots:
        config_roots = (
            runtime_root / "GPT_SoVITS" / "configs",
            runtime_root / "GPT-SoVITS" / "GPT_SoVITS" / "configs",
        )
        for config_root in config_roots:
            for name in _TTS_PROFILE_NAMES:
                path = config_root / name
                if not path.is_file():
                    continue
                relative = path.relative_to(tts_root.parent).as_posix()
                try:
                    raw = path.read_text(encoding="utf-8")
                    payload = yaml.safe_load(raw)
                    if not isinstance(payload, Mapping):
                        raise ValueError("profile root must be a mapping")
                    updated = _sanitize_tts_profile_payload(payload)
                    if updated == payload:
                        continue
                    encoded = yaml.safe_dump(updated, allow_unicode=True, sort_keys=False)
                    path.write_text(encoded, encoding="utf-8", newline="\n")
                except LegacyImportError:
                    raise
                except Exception as error:
                    raise LegacyImportError(
                        "LEGACY_TTS_CONFIG_VALIDATION_FAILED",
                        "validating",
                        relative,
                    ) from error
                changed += 1
                byte_delta += len(encoded.encode("utf-8")) - len(raw.encode("utf-8"))
    return changed, byte_delta


def _sanitize_tts_runtime_pth_files(tts_root: Path) -> tuple[int, int]:
    """Remove copied absolute install paths from Python ``.pth`` files."""

    if not tts_root.is_dir():
        return 0, 0
    changed = 0
    byte_delta = 0
    for path in tts_root.rglob("*.pth"):
        # Python only processes .pth files that are direct children of a
        # site-packages directory. Packages may also ship unrelated binary
        # model weights with the same suffix (for example torchmetrics LPIPS
        # weights), which must remain opaque during migration.
        if path.parent.name.casefold() != "site-packages":
            continue
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise LegacyImportError(
                "LEGACY_TTS_CONFIG_VALIDATION_FAILED",
                "validating",
                path.relative_to(tts_root.parent).as_posix(),
            ) from error
        kept = [
            line
            for line in raw.splitlines()
            if not _is_absolute_runtime_path(line.strip())
        ]
        if len(kept) == len(raw.splitlines()):
            continue
        if not any(line.strip() and not line.lstrip().startswith("#") for line in kept):
            kept = ["# Legacy absolute paths removed during Sakura import."]
        encoded = "\n".join(kept) + "\n"
        path.write_text(encoded, encoding="utf-8", newline="\n")
        changed += 1
        byte_delta += len(encoded.encode("utf-8")) - len(raw.encode("utf-8"))
    return changed, byte_delta


def _sanitize_tts_profile_payload(payload: Mapping[str, object]) -> dict[str, object]:
    updated = dict(payload)
    raw_custom = payload.get("custom")
    if not isinstance(raw_custom, Mapping):
        return updated
    custom = dict(raw_custom)
    version = str(custom.get("version") or "").strip()
    defaults = payload.get(version)
    defaults = defaults if isinstance(defaults, Mapping) else {}
    for field in _TTS_PROFILE_PATH_FIELDS:
        current = custom.get(field)
        if not _is_absolute_runtime_path(current):
            continue
        replacement = defaults.get(field)
        if not isinstance(replacement, str) or not replacement.strip():
            raise ValueError(f"managed profile has no bundled fallback for {field}")
        custom[field] = replacement
    updated["custom"] = custom
    return updated


def _is_absolute_runtime_path(value: object) -> bool:
    text = str(value or "").strip()
    return bool(text) and (Path(text).is_absolute() or PureWindowsPath(text).is_absolute())


def _copy_memory(
    source: Path,
    payload: Path,
    cancelled: CancelChecker,
    *,
    import_id: str = "direct-check",
) -> tuple[int, int]:
    """Copy Memory and replace the raw history copy with one SQLite snapshot.

    A WAL database is a logical unit made from the main database and its live
    sidecars.  Copying those files one after another can create a combination
    that never existed, especially when the old Sakura process still has the
    database open.  SQLite's backup API reads one consistent transaction and
    includes committed WAL pages without modifying the source database.
    """

    source_root = source / "data" / "memory"
    target_root = payload / "data" / "memory"
    source_files, source_bytes = tree_stats(source_root)
    _log_legacy_import(
        import_id,
        "legacy_import.memory_copy_started",
        "开始复制旧版本长期记忆",
        {
            "source_files": source_files,
            "source_bytes": source_bytes,
        },
    )
    copy_tree_checked(
        source_root,
        target_root,
        cancelled=cancelled,
        skip_noise=True,
    )
    source_history = source_root / "mem0_history.db"
    if source_history.is_file():
        _snapshot_sqlite_database(
            source_history,
            target_root / "mem0_history.db",
            cancelled,
            import_id=import_id,
        )
    return tree_stats(target_root)


def _snapshot_sqlite_database(
    source: Path,
    target: Path,
    cancelled: CancelChecker,
    *,
    import_id: str = "direct-check",
) -> None:
    temporary = target.with_name(f".{target.name}.snapshot-{uuid.uuid4().hex}")
    step = "inspect_source"
    progress_state = {"remaining_pages": 0, "total_pages": 0}

    def check_progress(_status: int, remaining: int, total: int) -> None:
        progress_state["remaining_pages"] = max(0, int(remaining))
        progress_state["total_pages"] = max(0, int(total))
        if cancelled():
            raise LegacyImportError("LEGACY_IMPORT_CANCELLED", "staging")

    try:
        _log_legacy_import(
            import_id,
            "legacy_import.memory_snapshot_started",
            "开始创建长期记忆 SQLite 快照",
            {
                "database_bytes": _safe_file_size(source),
                "wal_bytes": _safe_file_size(Path(f"{source}-wal")),
                "shm_bytes": _safe_file_size(Path(f"{source}-shm")),
            },
        )
        step = "open_source"
        source_uri = _sqlite_readonly_uri(source)
        with closing(sqlite3.connect(source_uri, uri=True, timeout=10)) as origin:
            row = origin.execute("PRAGMA journal_mode").fetchone()
            journal_mode = str(row[0]) if row else "unknown"
            page_count_row = origin.execute("PRAGMA page_count").fetchone()
            _log_legacy_import(
                import_id,
                "legacy_import.memory_snapshot_source_opened",
                "旧版本长期记忆数据库已打开",
                {
                    "journal_mode": journal_mode,
                    "page_count": int(page_count_row[0]) if page_count_row else 0,
                    "sqlite_version": sqlite3.sqlite_version,
                },
            )
            step = "open_snapshot"
            with closing(sqlite3.connect(temporary)) as snapshot:
                step = "backup"
                origin.backup(snapshot, pages=256, progress=check_progress, sleep=0.05)
                step = "quick_check"
                result = snapshot.execute("PRAGMA quick_check").fetchone()
                if result is None or result[0] != "ok":
                    raise sqlite3.DatabaseError("SQLite backup failed quick_check")

        step = "install_snapshot"
        for suffix in ("-wal", "-shm", "-journal"):
            Path(f"{target}{suffix}").unlink(missing_ok=True)
        temporary.replace(target)
        _log_legacy_import(
            import_id,
            "legacy_import.memory_snapshot_completed",
            "长期记忆 SQLite 快照创建完成",
            {
                **progress_state,
                "snapshot_bytes": _safe_file_size(target),
                "quick_check": "ok",
            },
        )
    except LegacyImportError as exc:
        _log_legacy_import(
            import_id,
            "legacy_import.memory_snapshot_failed",
            "长期记忆 SQLite 快照创建失败",
            {
                "detail_stage": step,
                **progress_state,
                **_exception_log_attributes(exc, stage=step),
            },
            severity="warning" if exc.code == "LEGACY_IMPORT_CANCELLED" else "error",
        )
        temporary.unlink(missing_ok=True)
        raise
    except (OSError, sqlite3.Error) as exc:
        _log_legacy_import(
            import_id,
            "legacy_import.memory_snapshot_failed",
            "长期记忆 SQLite 快照创建失败",
            {
                "detail_stage": step,
                **progress_state,
                **_exception_log_attributes(exc, stage=step),
            },
            severity="error",
        )
        temporary.unlink(missing_ok=True)
        raise LegacyImportError(
            "LEGACY_MEMORY_DATABASE_INVALID",
            "staging",
            "data/memory/mem0_history.db",
        ) from exc


def _safe_file_size(path: Path) -> int:
    try:
        return path.stat().st_size if path.is_file() else 0
    except OSError:
        return -1


def _sqlite_readonly_uri(path: Path) -> str:
    r"""Build a SQLite URI from normal or Windows extended-length paths.

    Tauri's directory picker canonicalizes Windows selections to ``\\?\D:\``.
    ``Path.as_uri`` encodes that prefix as a URI authority named ``%3F``, which
    SQLite rejects before reading the database.  Strip only the Win32 namespace
    prefix while retaining the resolved path and read-only URI semantics.
    """

    resolved = str(path.resolve(strict=True))
    if os.name == "nt":
        if resolved.startswith("\\\\?\\UNC\\"):
            resolved = "\\\\" + resolved[8:]
        elif resolved.startswith("\\\\?\\"):
            resolved = resolved[4:]
    return f"{Path(resolved).as_uri()}?mode=ro"


def _exception_log_attributes(
    error: BaseException, *, stage: str = "internal"
) -> dict[str, object]:
    diagnostic = (
        str(error.strerror or type(error).__name__)
        if isinstance(error, OSError)
        else str(error)
    )
    reason_code = getattr(error, "code", None) or getattr(
        error, "sqlite_errorname", None
    )
    if not reason_code:
        winerror = getattr(error, "winerror", None)
        errno = getattr(error, "errno", None)
        reason_code = (
            f"WINERROR_{winerror}"
            if winerror is not None
            else f"ERRNO_{errno}"
            if errno is not None
            else "LEGACY_IMPORT_FAILED"
        )
    attributes: dict[str, object] = {
        "diagnostic": diagnostic,
        "error_type": type(error).__name__,
        "reason_code": str(reason_code),
        "stage": stage,
    }
    for name in ("sqlite_errorcode", "sqlite_errorname", "errno", "winerror"):
        value = getattr(error, name, None)
        if value is not None:
            attributes[name] = value
    return attributes


def _log_legacy_import(
    import_id: str,
    event: str,
    message: str,
    attributes: Mapping[str, object] | None = None,
    *,
    severity: str = "info",
) -> None:
    try:
        _DIAGNOSTIC_SINK.get()(
            event,
            message,
            {"import_id": import_id, **dict(attributes or {})},
            severity,
        )
    except Exception:
        # Diagnostics must never change the migration transaction result.
        return


def _copy_legacy_onnx(
    source: Path,
    payload: Path,
    cancelled: CancelChecker,
    *,
    match_characters: bool = True,
) -> tuple[int, int]:
    legacy_onnx = source / "data" / "tts_bundles" / "onnx"
    if not legacy_onnx.is_dir():
        return 0, 0
    character_dirs = (
        {
            child.name.casefold(): child.name
            for child in (payload / "characters").iterdir()
            if child.is_dir()
        }
        if match_characters and (payload / "characters").is_dir()
        else {}
    )
    files = total = 0
    for child in sorted(legacy_onnx.iterdir(), key=lambda path: path.name.casefold()):
        if is_link_or_junction(child):
            raise LegacyImportError("LEGACY_NESTED_LINK_UNSUPPORTED", "staging")
        character_id = character_dirs.get(child.name.casefold()) if child.is_dir() else None
        destination = (
            payload / "characters" / character_id / "voice" / "onnx"
            if character_id
            else payload / "tts" / "onnx" / child.name
        )
        if child.is_dir():
            child_files, child_bytes = copy_tree_checked(
                child,
                destination,
                cancelled=cancelled,
                skip_noise=True,
                allow_identical_existing=True,
            )
            files += child_files
            total += child_bytes
        elif child.is_file():
            copied = copy_file_checked(
                child,
                destination,
                cancelled=cancelled,
                allow_identical_existing=True,
            )
            total += copied
            files += 1
    return files, total


def _copy_other_user_data(
    source: Path,
    payload: Path,
    cancelled: CancelChecker,
    report: ImportReport,
) -> None:
    data = source / "data"
    direct_dirs = ("notes", "character_studio")
    for name in direct_dirs:
        files, size = copy_tree_checked(
            data / name, payload / "data" / name, cancelled=cancelled, skip_noise=True
        )
        if files:
            report.counts[name] = files
            report.bytes[name] = size
    for name in ("reminders.json", "tasks.json", "screen_awareness_state.json"):
        path = data / name
        if path.is_file():
            size = copy_file_checked(path, payload / "data" / name, cancelled=cancelled)
            report.counts[name] = 1
            report.bytes[name] = size

    mobile = data / "plugins" / "sakura_mobile"
    if mobile.is_dir():
        files, size = copy_tree_checked(
            mobile,
            payload / "data" / "plugins" / "sakura_mobile",
            cancelled=cancelled,
            skip_noise=True,
        )
        report.counts["sakuraMobileFiles"] = files
        report.bytes["sakuraMobile"] = size

    quarantine = payload / "data" / "legacy-imports" / report.import_id / "quarantine"
    for relative, destination in (
        (Path("data/chat_history"), Path("chat-history")),
        (Path("data/visual_observations"), Path("visual-observations")),
        (Path("data/runtime_events"), Path("runtime-events")),
    ):
        files, size = copy_tree_checked(
            source / relative,
            quarantine / destination,
            cancelled=cancelled,
            skip_noise=True,
        )
        if files:
            report.quarantined.append(
                {"kind": destination.as_posix(), "files": files, "bytes": size}
            )
    plugin_data = data / "plugins"
    if plugin_data.is_dir():
        for child in plugin_data.iterdir():
            if child.is_dir() and child.name != "sakura_mobile":
                files, size = copy_tree_checked(
                    child,
                    quarantine / "plugin-data" / child.name,
                    cancelled=cancelled,
                    skip_noise=True,
                )
                report.quarantined.append(
                    {"kind": "plugin-data", "id": child.name[:80], "files": files, "bytes": size}
                )
    legacy_plugins = source / "plugins"
    if legacy_plugins.is_dir():
        files, size = copy_tree_checked(
            legacy_plugins,
            quarantine / "plugin-code",
            cancelled=cancelled,
            skip_noise=True,
        )
        if files:
            report.quarantined.append({"kind": "plugin-code", "files": files, "bytes": size})
    memory_json = data / "memory.json"
    if memory_json.is_file():
        size = copy_file_checked(memory_json, quarantine / "memory.json", cancelled=cancelled)
        report.quarantined.append({"kind": "legacy-memory-json", "files": 1, "bytes": size})


def _validate_staged(staged: Path, *, import_id: str = "direct-check") -> None:
    timeline = TimelineStore(staged / "data" / "chat_history" / "timeline.sqlite3")
    timeline.assert_activated()
    config = CoreConfigReader().read(staged)
    if config.config_problem is not None and config.config_problem.state == "failed":
        raise LegacyImportError(config.config_problem.code, "validating")
    _validate_current_settings(staged)
    try:
        load_mcp_config(staged / "config" / "mcp.yaml")
    except Exception as exc:  # noqa: BLE001 - expose only a stable, content-free code
        raise LegacyImportError(
            "LEGACY_MCP_VALIDATION_FAILED", "validating", "config/mcp.yaml"
        ) from exc
    try:
        ReminderStore(staged / "data" / "reminders.json").list_reminders({})
    except Exception as exc:  # noqa: BLE001 - legacy content must not cross the boundary
        raise LegacyImportError(
            "LEGACY_REMINDERS_VALIDATION_FAILED", "validating", "data/reminders.json"
        ) from exc
    try:
        TodoStore(staged / "data" / "tasks.json").list_todos({})
    except Exception as exc:  # noqa: BLE001 - legacy content must not cross the boundary
        raise LegacyImportError(
            "LEGACY_TASKS_VALIDATION_FAILED", "validating", "data/tasks.json"
        ) from exc
    _validate_character_studio(staged)
    _validate_notes_and_screen_state(staged)


def _validate_current_settings(staged: Path) -> None:
    """Exercise the same loaders used by settings/Core before committing.

    CoreConfigReader intentionally covers only Core startup. Settings domains
    such as screen awareness and plugin desired state have stricter current
    schemas, so validating only the Core projection can accept a migration
    that later leaves the settings UI unreadable.
    """

    service = AppSettingsService(staged)
    loaders: tuple[tuple[str, str, Callable[[], object]], ...] = (
        ("api", "config/api.yaml", service.load_api_settings),
        ("api_profiles", "config/api.yaml", service.load_api_profiles),
        ("model_selection", "config/api.yaml", service.load_model_selection),
        ("runtime_loop", "config/system_config.yaml", service.load_runtime_loop_settings),
        ("debug_log", "config/system_config.yaml", service.load_debug_log_settings),
        ("startup", "config/system_config.yaml", service.load_startup_settings),
        ("theme", "config/system_config.yaml", service.load_theme_settings),
        (
            "character_theme_overrides",
            "config/system_config.yaml",
            service.load_character_theme_overrides,
        ),
        (
            "screen_awareness",
            "config/system_config.yaml",
            service.load_screen_awareness_settings,
        ),
        ("bubble", "config/system_config.yaml", service.load_bubble_settings),
        ("backchannel", "config/system_config.yaml", service.load_backchannel_settings),
        ("plugins", "config/plugins.yaml", PluginDesiredStateStore(staged).read),
    )
    for _name, relative, loader in loaders:
        try:
            loader()
        except Exception as exc:  # noqa: BLE001 - keep user data out of the public error
            raise LegacyImportError(
                "LEGACY_SETTINGS_VALIDATION_FAILED", "validating", relative
            ) from exc


def _validate_characters(
    staged: Path,
    *,
    import_id: str,
    failure_severity: str = "error",
) -> None:
    registry = CharacterRegistry(staged, issue_sink=lambda *_args: None)
    if registry.load_errors:
        first = registry.load_errors[0]
        relative = first.manifest_path.relative_to(staged).as_posix()
        safe_error = re.sub(
            re.escape(str(staged)),
            "<staging>",
            first.error,
            flags=re.IGNORECASE,
        )
        _log_legacy_import(
            import_id,
            "legacy_import.character_validation_failed",
            "迁移后的角色包校验失败",
            {
                "detail_stage": "characters",
                "relative_path": relative,
                "validation_error": safe_error,
            },
            severity=failure_severity,
        )
        raise LegacyImportError("LEGACY_CHARACTER_VALIDATION_FAILED", "validating", relative)


def _validate_tts_configs(staged: Path) -> None:
    validators = (
        (
            staged / "data/plugins/sakura.tts.gpt-sovits/config.json",
            "plugins.builtin.sakura_gpt_sovits.plugin",
        ),
        (
            staged / "data/plugins/sakura.tts.genie/config.json",
            "plugins.builtin.sakura_genie.plugin",
        ),
    )
    for path, module_name in validators:
        if not path.is_file():
            continue
        relative = path.relative_to(staged).as_posix()
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError("invalid TTS config")
            module = __import__(module_name, fromlist=["_parse_config"])
            module._parse_config(value)
        except Exception as exc:  # noqa: BLE001 - provider details remain private
            raise LegacyImportError(
                "LEGACY_TTS_CONFIG_VALIDATION_FAILED", "validating", relative
            ) from exc


def _validate_character_studio(staged: Path) -> None:
    root = staged / "data" / "character_studio"
    if not root.is_dir():
        return
    service = CharacterStudioService(staged, workspace_root=root)
    for path in sorted((root / "drafts").glob("*/draft.json")):
        relative = path.relative_to(staged).as_posix()
        try:
            state = service._read_state(path.parent.name)
            if state is None:
                raise ValueError("missing draft state")
            CharacterStudioDoc.from_payload(state["doc"])
        except Exception as exc:  # noqa: BLE001 - draft contents remain private
            raise LegacyImportError(
                "LEGACY_CHARACTER_STUDIO_VALIDATION_FAILED", "validating", relative
            ) from exc


def _validate_notes_and_screen_state(staged: Path) -> None:
    notes_root = staged / "data" / "notes"
    if notes_root.is_dir():
        store = NotesStore(notes_root)
        for path in sorted(item for item in notes_root.rglob("*") if item.is_file()):
            relative = path.relative_to(staged).as_posix()
            try:
                if path.parent != notes_root or path.suffix.casefold() != ".txt":
                    raise ValueError("unsupported note path")
                store.read_note({"name": path.name})
            except Exception as exc:  # noqa: BLE001 - note contents remain private
                raise LegacyImportError(
                    "LEGACY_NOTE_VALIDATION_FAILED", "validating", relative
                ) from exc
    screen = staged / "data" / "screen_awareness_state.json"
    if screen.is_file():
        try:
            value = json.loads(screen.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise LegacyImportError(
                "LEGACY_SCREEN_STATE_VALIDATION_FAILED",
                "validating",
                "data/screen_awareness_state.json",
            ) from exc
        if not isinstance(value, dict):
            raise LegacyImportError(
                "LEGACY_SCREEN_STATE_VALIDATION_FAILED",
                "validating",
                "data/screen_awareness_state.json",
            )


def _validate_memory(root: Path) -> None:
    if not root.exists():
        return
    history = root / "mem0_history.db"
    if history.is_file():
        database = history
        if not database.is_file():
            return
        relative = database.relative_to(root).as_posix()
        # Shared-memory files are process-local coordination state.  The copy
        # step already replaced the raw WAL triplet with a consistent SQLite
        # backup, but clean up stale sidecars as a defensive measure for direct
        # validator callers and older staging directories.
        Path(f"{database}-shm").unlink(missing_ok=True)
        try:
            with closing(sqlite3.connect(database)) as connection:
                result = connection.execute("PRAGMA quick_check").fetchone()
                checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        except sqlite3.DatabaseError as exc:
            raise LegacyImportError(
                "LEGACY_MEMORY_DATABASE_INVALID", "validating", relative
            ) from exc
        if result is None or result[0] != "ok" or (checkpoint and checkpoint[0] != 0):
            raise LegacyImportError(
                "LEGACY_MEMORY_DATABASE_INVALID", "validating", relative
            )
        Path(f"{database}-shm").unlink(missing_ok=True)
        wal = Path(f"{database}-wal")
        if wal.is_file() and wal.stat().st_size == 0:
            wal.unlink()
        try:
            # Normalize through the same SQLite manager Core will use after
            # commit.  Keeping a second handwritten schema gate here caused
            # valid legacy variants to pass standalone database checks but be
            # rejected by the importer (or vice versa).  This operates only on
            # the staging copy; the legacy database remains byte-for-byte
            # untouched.
            from plugins.builtin.sakura_mem0.memory import (
                normalize_existing_history_database,
            )

            normalize_existing_history_database(history)
            with closing(sqlite3.connect(history)) as connection:
                result = connection.execute("PRAGMA quick_check").fetchone()
                checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                if result is None or result[0] != "ok":
                    raise LegacyImportError(
                        "LEGACY_MEMORY_DATABASE_INVALID", "validating", relative
                    )
                if checkpoint and checkpoint[0] != 0:
                    raise LegacyImportError(
                        "LEGACY_MEMORY_DATABASE_INVALID", "validating", relative
                    )
        except sqlite3.DatabaseError as exc:
            raise LegacyImportError(
                "LEGACY_MEMORY_SCHEMA_INVALID", "validating", relative
            ) from exc
        except LegacyImportError:
            raise
        except Exception as exc:  # noqa: BLE001 - schema details remain private
            raise LegacyImportError(
                "LEGACY_MEMORY_SCHEMA_INVALID", "validating", relative
            ) from exc
        Path(f"{history}-shm").unlink(missing_ok=True)
        wal = Path(f"{history}-wal")
        if wal.is_file() and wal.stat().st_size == 0:
            wal.unlink()
def _legacy_curation(source: Path) -> tuple[dict[str, int], str]:
    current = ""
    config = source / "data" / "config" / "characters.yaml"
    if config.is_file():
        try:
            value = yaml.safe_load(config.read_text(encoding="utf-8"))
            current = str(value.get("current_character_id") or "") if isinstance(value, dict) else ""
        except (OSError, UnicodeError, yaml.YAMLError):
            current = ""
    current = current.strip()
    counts: dict[str, int] = {}
    global_path = source / "data" / "memory_curation_state.json"
    if current:
        counts[current] = _read_processed_count(global_path)
    for path in sorted((source / "data").glob("memory_curation_state.*.json")):
        scope = path.name.removeprefix("memory_curation_state.").removesuffix(".json").strip()
        if scope:
            counts[scope] = _read_processed_count(path)
    return {scope: count for scope, count in counts.items() if count > 0}, current


def _read_processed_count(path: Path) -> int:
    if not path.is_file():
        return 0
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise TypeError("curation state must be an object")
        return max(0, int(value.get("processed_history_count", 0)))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise LegacyImportError(
            "LEGACY_CURATION_STATE_INVALID", "staging", f"data/{path.name}"
        ) from exc


def _mapped_processed_counts(
    counts: Mapping[str, int], ids: tuple[str, ...]
) -> dict[str, int]:
    mapped: dict[str, int] = {}
    for scope, processed in counts.items():
        if processed <= 0:
            continue
        exact = next((value for value in ids if value == scope), None)
        matches = [value for value in ids if value.casefold() == scope.casefold()]
        mapped[exact or (matches[0] if len(matches) == 1 else scope)] = processed
    return mapped


def _write_curation_states(
    staged: Path,
    counts: Mapping[str, int],
    history: HistoryImportStats,
) -> None:
    used_targets: set[str] = set()
    for scope, processed in counts.items():
        if processed <= 0:
            continue
        entry_id = history.cutoff_entry_ids.get(scope, "")
        cursor = _cursor_for_entry(
            staged / "data" / "chat_history" / "timeline.sqlite3", scope, entry_id
        )
        total = history.per_character_records.get(scope, processed)
        state = {
            "processed_history_count": min(processed, total),
            "pending_turns": 0,
            "backfill_completed": processed >= total,
            "timeline_sync_cursor": cursor,
            "curation_cursor": cursor,
        }
        safe = "".join(
            character if character.isalnum() or character in "._-" else "_"
            for character in scope
        )
        if not safe or safe.casefold() in used_targets:
            raise LegacyImportError("LEGACY_CURATION_SCOPE_CONFLICT", "validating")
        used_targets.add(safe.casefold())
        target = staged / "data" / "memory" / "curation_state" / f"{safe}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


def _cursor_for_entry(database: Path, character_id: str, entry_id: str) -> str:
    if not entry_id:
        return ""
    with closing(sqlite3.connect(database)) as connection:
        lineage = int(connection.execute("PRAGMA application_id").fetchone()[0])
        row = connection.execute(
            "SELECT seq FROM timeline_entries WHERE character_id = ? AND entry_id = ?",
            (character_id, entry_id),
        ).fetchone()
    return _encode_cursor(character_id, lineage, int(row[0]), entry_id) if row else ""


def _write_report(payload: Path, report: ImportReport) -> None:
    target = payload / "data" / "legacy-imports" / report.import_id / "report.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(report.to_public_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _build_artifact_manifest(
    payload: Path,
    cancelled: CancelChecker,
    *,
    byte_progress: Callable[[int, int], None] | None = None,
) -> list[dict[str, object]]:
    paths = sorted(item for item in payload.rglob("*") if item.is_file())
    try:
        expected_bytes = sum(path.stat().st_size for path in paths)
    except OSError as exc:
        raise LegacyImportError("LEGACY_STAGED_VALIDATION_FAILED", "validating") from exc
    completed_bytes = 0
    if byte_progress is not None:
        byte_progress(0, expected_bytes)

    def completed(artifact: dict[str, object]) -> None:
        nonlocal completed_bytes
        completed_bytes += int(artifact["bytes"])
        if byte_progress is not None:
            byte_progress(completed_bytes, expected_bytes)

    if len(paths) < 32:
        artifacts = []
        for path in paths:
            artifact = _build_artifact(payload, path, cancelled)
            artifacts.append(artifact)
            completed(artifact)
        return artifacts

    artifacts: list[dict[str, object]] = []
    pending: deque[tuple[Path, Future[dict[str, object]]]] = deque()
    iterator = iter(paths)
    workers = 8
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="legacy-import-hash") as pool:
        for path in iterator:
            _check_cancelled(cancelled, stage="validating")
            pending.append((path, pool.submit(_build_artifact, payload, path, cancelled)))
            if len(pending) < workers * 4:
                continue
            _path, future = pending.popleft()
            artifact = future.result()
            artifacts.append(artifact)
            completed(artifact)
        while pending:
            _path, future = pending.popleft()
            artifact = future.result()
            artifacts.append(artifact)
            completed(artifact)
    return artifacts


def _build_artifact(
    payload: Path,
    path: Path,
    cancelled: CancelChecker,
) -> dict[str, object]:
    _check_cancelled(cancelled, stage="validating")
    relative = path.relative_to(payload).as_posix()
    try:
        size = path.stat().st_size
        digest = sha256_file(path, cancelled=cancelled)
    except LegacyImportError:
        raise
    except OSError as exc:
        raise LegacyImportError(
            "LEGACY_STAGED_VALIDATION_FAILED", "validating", relative
        ) from exc
    return {
        "domain": _artifact_domain(relative),
        "id": relative,
        "bytes": size,
        "sha256": digest,
    }


def _artifact_domain(relative: str) -> str:
    if relative.startswith("characters/"):
        return "characters"
    if relative.startswith("config/"):
        return "config"
    if relative.startswith("tts/"):
        return "tts"
    if relative.startswith("data/chat_history/"):
        return "history"
    if relative.startswith("data/memory/"):
        return "memory"
    if "/quarantine/" in relative:
        return "quarantine"
    return "data"


def _check_cancelled(cancelled: CancelChecker, *, stage: str = "staging") -> None:
    if cancelled():
        raise LegacyImportError("LEGACY_IMPORT_CANCELLED", stage)


__all__ = [
    "inspect_legacy_installation",
    "run_legacy_import",
    "finalize_commit",
    "rollback_commit",
    "PendingCommit",
]
