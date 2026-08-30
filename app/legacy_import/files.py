from __future__ import annotations

import hashlib
import locale
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping
from pathlib import Path

from .errors import LegacyImportError


CancelChecker = Callable[[], bool]
Progress = Callable[[str, int, str], None]
CopyDiagnostic = Callable[[str, Mapping[str, object]], None]
CopyByteProgress = Callable[[int, int], None]

_SKIP_NAMES = {
    ".lock",
    "__pycache__",
    ".pytest_cache",
    "logs",
    "diagnostics",
    "cache",
    "migration_backup",
    "migration_backups",
    "_tmp",
    "tmp",
}
_SKIP_SUFFIXES = (".log", ".lock", ".tmp", ".part", ".pyc")


def is_link_or_junction(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        checker = getattr(path, "is_junction", None)
        return bool(checker and checker())
    except OSError:
        return True


def safe_relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError as exc:
        raise LegacyImportError("LEGACY_PATH_ESCAPE", "inspect") from exc


def tree_stats(root: Path, *, follow_root_link: bool = False) -> tuple[int, int]:
    if not root.exists():
        return 0, 0
    actual = root.resolve(strict=True) if follow_root_link else root
    files = total = 0
    stack = [actual]
    while stack:
        directory = stack.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError as exc:
            raise LegacyImportError("LEGACY_SOURCE_UNREADABLE", "inspect") from exc
        for entry in entries:
            path = Path(entry.path)
            if entry.is_symlink():
                continue
            if entry.is_dir(follow_symlinks=False):
                stack.append(path)
            elif entry.is_file(follow_symlinks=False):
                files += 1
                try:
                    total += entry.stat(follow_symlinks=False).st_size
                except OSError as exc:
                    raise LegacyImportError("LEGACY_SOURCE_UNREADABLE", "inspect") from exc
    return files, total


def sha256_file(path: Path, *, cancelled: CancelChecker | None = None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            if cancelled is not None and cancelled():
                raise LegacyImportError("LEGACY_IMPORT_CANCELLED", "validating")
            digest.update(chunk)
    return digest.hexdigest()


def _files_identical(source: Path, target: Path, cancelled: CancelChecker) -> bool:
    try:
        if source.stat().st_size != target.stat().st_size:
            return False
        with source.open("rb") as left, target.open("rb") as right:
            while True:
                if cancelled():
                    raise LegacyImportError("LEGACY_IMPORT_CANCELLED", "staging")
                left_chunk = left.read(1024 * 1024)
                right_chunk = right.read(1024 * 1024)
                if left_chunk != right_chunk:
                    return False
                if not left_chunk:
                    return True
    except LegacyImportError:
        raise
    except OSError as exc:
        raise LegacyImportError("LEGACY_COPY_FAILED", "staging") from exc


def copy_file_checked(
    source: Path,
    target: Path,
    *,
    cancelled: CancelChecker,
    allow_identical_existing: bool = False,
) -> int:
    if cancelled():
        raise LegacyImportError("LEGACY_IMPORT_CANCELLED", "staging")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if (
            allow_identical_existing
            and target.is_file()
            and _files_identical(source, target, cancelled)
        ):
            return 0
        raise LegacyImportError("LEGACY_COPY_CONFLICT", "staging")
    copied = 0
    try:
        with source.open("rb") as reader, target.open("xb") as writer:
            while chunk := reader.read(1024 * 1024):
                if cancelled():
                    raise LegacyImportError("LEGACY_IMPORT_CANCELLED", "staging")
                writer.write(chunk)
                copied += len(chunk)
            writer.flush()
            os.fsync(writer.fileno())
        shutil.copystat(source, target, follow_symlinks=False)
    except LegacyImportError:
        target.unlink(missing_ok=True)
        raise
    except OSError as exc:
        target.unlink(missing_ok=True)
        raise LegacyImportError("LEGACY_COPY_FAILED", "staging") from exc
    return copied


def copy_tree_checked(
    source: Path,
    target: Path,
    *,
    cancelled: CancelChecker,
    skip_noise: bool = False,
    noise_names_at_root_only: bool = False,
    reject_links: bool = True,
    allow_identical_existing: bool = False,
    _noise_depth: int = 0,
) -> tuple[int, int]:
    if not source.exists():
        return 0, 0
    if is_link_or_junction(source):
        raise LegacyImportError("LEGACY_NESTED_LINK_UNSUPPORTED", "staging")
    files = total = 0
    target.mkdir(parents=True, exist_ok=True)
    for entry in sorted(os.scandir(source), key=lambda item: item.name.casefold()):
        if cancelled():
            raise LegacyImportError("LEGACY_IMPORT_CANCELLED", "staging")
        name = entry.name
        folded_name = name.casefold()
        if skip_noise and (
            folded_name.endswith(_SKIP_SUFFIXES)
            or (
                folded_name in _SKIP_NAMES
                and (not noise_names_at_root_only or _noise_depth == 0)
            )
        ):
            continue
        child_source = Path(entry.path)
        child_target = target / name
        if entry.is_symlink() or is_link_or_junction(child_source):
            if reject_links:
                raise LegacyImportError("LEGACY_NESTED_LINK_UNSUPPORTED", "staging")
            continue
        if entry.is_dir(follow_symlinks=False):
            child_files, child_bytes = copy_tree_checked(
                child_source,
                child_target,
                cancelled=cancelled,
                skip_noise=skip_noise,
                noise_names_at_root_only=noise_names_at_root_only,
                reject_links=reject_links,
                allow_identical_existing=allow_identical_existing,
                _noise_depth=_noise_depth + 1,
            )
            files += child_files
            total += child_bytes
        elif entry.is_file(follow_symlinks=False):
            if child_target.exists():
                if (
                    allow_identical_existing
                    and child_target.is_file()
                    and _files_identical(child_source, child_target, cancelled)
                ):
                    continue
                raise LegacyImportError("LEGACY_COPY_CONFLICT", "staging")
            total += copy_file_checked(child_source, child_target, cancelled=cancelled)
            files += 1
    return files, total


def copy_tree_fast_checked(
    source: Path,
    target: Path,
    *,
    cancelled: CancelChecker,
    skip_noise: bool = False,
    noise_names_at_root_only: bool = False,
    diagnostic: CopyDiagnostic | None = None,
    byte_progress: CopyByteProgress | None = None,
) -> tuple[int, int]:
    """Copy a new directory tree with a guarded Windows robocopy fast path.

    Legacy TTS bundles contain tens of thousands of small files.  Calling
    ``fsync`` once per file is needlessly slow for an isolated staging tree,
    while robocopy can populate that empty tree concurrently.  Preflight and
    post-copy scans retain the importer's link, exclusion, count, and size
    invariants.  Existing destinations keep the precise Python conflict
    semantics, and non-Windows hosts use the normal copier.
    """

    robocopy = shutil.which("robocopy") if os.name == "nt" else None
    if robocopy is None or target.exists():
        _emit_copy_diagnostic(
            diagnostic,
            "started",
            {"detail_stage": "python_copy", "copy_method": "python"},
        )
        try:
            result = copy_tree_checked(
                source,
                target,
                cancelled=cancelled,
                skip_noise=skip_noise,
                noise_names_at_root_only=noise_names_at_root_only,
            )
        except Exception:
            _emit_copy_diagnostic(
                diagnostic,
                "failed",
                {"detail_stage": "python_copy", "copy_method": "python"},
            )
            raise
        _emit_copy_diagnostic(
            diagnostic,
            "completed",
            {
                "detail_stage": "python_copy",
                "copy_method": "python",
                "actual_files": result[0],
                "actual_bytes": result[1],
            },
        )
        return result
    if not source.exists():
        return 0, 0
    if is_link_or_junction(source):
        raise LegacyImportError("LEGACY_NESTED_LINK_UNSUPPORTED", "staging")

    _emit_copy_diagnostic(
        diagnostic,
        "started",
        {"detail_stage": "preflight", "copy_method": "robocopy"},
    )
    try:
        expected_files, expected_bytes = _copy_tree_stats_checked(
            source,
            cancelled=cancelled,
            skip_noise=skip_noise,
            noise_names_at_root_only=noise_names_at_root_only,
        )
    except Exception:
        _emit_copy_diagnostic(
            diagnostic,
            "failed",
            {"detail_stage": "preflight", "copy_method": "robocopy"},
        )
        raise
    _emit_copy_diagnostic(
        diagnostic,
        "preflight_completed",
        {
            "detail_stage": "preflight",
            "copy_method": "robocopy",
            "source_files": expected_files,
            "source_bytes": expected_bytes,
        },
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    if expected_files == 0:
        target.mkdir()
        if byte_progress is not None:
            byte_progress(0, 0)
        return 0, 0

    source_argument = _windows_cli_path(source)
    target_argument = _windows_cli_path(target)
    command = [
        robocopy,
        source_argument,
        target_argument,
        "/E",
        "/COPY:DAT",
        "/DCOPY:DAT",
        "/R:1",
        "/W:1",
        "/MT:16",
        "/XJ",
        "/NFL",
        "/NDL",
        "/NJH",
        "/NJS",
        "/NP",
    ]
    if skip_noise:
        excluded_names = (
            [_windows_cli_path(source / name) for name in _SKIP_NAMES]
            if noise_names_at_root_only
            else list(_SKIP_NAMES)
        )
        command.extend(["/XD", *excluded_names])
        command.extend(
            [
                "/XF",
                *excluded_names,
                *(f"*{suffix}" for suffix in _SKIP_SUFFIXES),
            ]
        )
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    _emit_copy_diagnostic(
        diagnostic,
        "robocopy_started",
        {
            "detail_stage": "robocopy",
            "copy_method": "robocopy",
            "source_files": expected_files,
            "source_bytes": expected_bytes,
            "extended_source_path_normalized": source_argument != str(source),
            "extended_target_path_normalized": target_argument != str(target),
        },
    )
    if byte_progress is not None:
        byte_progress(0, expected_bytes)
    try:
        output_file = tempfile.TemporaryFile()
    except OSError as exc:
        _emit_copy_diagnostic(
            diagnostic,
            "robocopy_failed",
            {"detail_stage": "robocopy", "copy_method": "robocopy"},
        )
        raise LegacyImportError("LEGACY_COPY_FAILED", "staging") from exc
    with output_file:
        try:
            process = subprocess.Popen(
                command,
                stdout=output_file,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
            )
        except OSError as exc:
            _emit_copy_diagnostic(
                diagnostic,
                "robocopy_failed",
                {"detail_stage": "robocopy", "copy_method": "robocopy"},
            )
            raise LegacyImportError("LEGACY_COPY_FAILED", "staging") from exc

        next_progress_scan = time.monotonic() + 1.0
        while process.poll() is None:
            if cancelled():
                _stop_copy_process(process)
                shutil.rmtree(target, ignore_errors=True)
                raise LegacyImportError("LEGACY_IMPORT_CANCELLED", "staging")
            if byte_progress is not None and time.monotonic() >= next_progress_scan:
                copied_bytes = _best_effort_tree_bytes(target)
                byte_progress(min(copied_bytes, expected_bytes), expected_bytes)
                next_progress_scan = time.monotonic() + 3.0
            time.sleep(0.05)
        output_tail = _robocopy_output_tail(
            output_file,
            sensitive_paths=(str(source), source_argument, str(target), target_argument),
        )
    # Robocopy uses bit flags: 0-7 are successful outcomes; bit 3 (8) and
    # above indicate at least one failed copy.
    completion = {
        "detail_stage": "robocopy",
        "copy_method": "robocopy",
        "return_code": process.returncode,
    }
    if output_tail:
        completion["output_tail"] = output_tail
    _emit_copy_diagnostic(diagnostic, "robocopy_completed", completion)
    if process.returncode is None or process.returncode >= 8:
        shutil.rmtree(target, ignore_errors=True)
        raise LegacyImportError("LEGACY_COPY_FAILED", "staging")

    try:
        actual_files, actual_bytes = _copy_tree_stats_checked(
            target,
            cancelled=cancelled,
            skip_noise=False,
        )
    except LegacyImportError:
        _emit_copy_diagnostic(
            diagnostic,
            "failed",
            {"detail_stage": "post_scan", "copy_method": "robocopy"},
        )
        shutil.rmtree(target, ignore_errors=True)
        raise
    comparison = {
        "detail_stage": "post_scan",
        "copy_method": "robocopy",
        "expected_files": expected_files,
        "expected_bytes": expected_bytes,
        "actual_files": actual_files,
        "actual_bytes": actual_bytes,
    }
    if (actual_files, actual_bytes) != (expected_files, expected_bytes):
        _emit_copy_diagnostic(diagnostic, "failed", comparison)
        shutil.rmtree(target, ignore_errors=True)
        raise LegacyImportError("LEGACY_COPY_FAILED", "staging")
    _emit_copy_diagnostic(diagnostic, "completed", comparison)
    if byte_progress is not None:
        byte_progress(actual_bytes, expected_bytes)
    return actual_files, actual_bytes


def _windows_cli_path(path: Path) -> str:
    """Remove the Win32 namespace prefix unsupported by tools such as robocopy."""

    value = str(path)
    if os.name != "nt":
        return value
    if value.startswith("\\\\?\\UNC\\"):
        return "\\\\" + value[8:]
    if value.startswith("\\\\?\\"):
        return value[4:]
    return value


def _emit_copy_diagnostic(
    callback: CopyDiagnostic | None,
    event: str,
    attributes: Mapping[str, object],
) -> None:
    if callback is not None:
        callback(event, attributes)


def _best_effort_tree_bytes(root: Path) -> int:
    if not root.exists():
        return 0
    total = 0
    stack = [root]
    while stack:
        directory = stack.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    stack.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    total += entry.stat(follow_symlinks=False).st_size
            except OSError:
                continue
    return total


def _robocopy_output_tail(
    output_file: object,
    *,
    sensitive_paths: tuple[str, ...],
) -> str:
    try:
        output_file.flush()  # type: ignore[attr-defined]
        output_file.seek(0, os.SEEK_END)  # type: ignore[attr-defined]
        size = output_file.tell()  # type: ignore[attr-defined]
        output_file.seek(max(0, size - 16 * 1024))  # type: ignore[attr-defined]
        raw = output_file.read()  # type: ignore[attr-defined]
    except (OSError, AttributeError):
        return ""
    encoding = locale.getpreferredencoding(False) or "utf-8"
    text = raw.decode(encoding, errors="replace") if isinstance(raw, bytes) else str(raw)
    for path in sorted({value for value in sensitive_paths if value}, key=len, reverse=True):
        text = re.sub(re.escape(path), "<path>", text, flags=re.IGNORECASE)
    lines = [line.strip() for line in text.replace("\r", "\n").split("\n") if line.strip()]
    return " | ".join(lines[-8:])[:2000]


def _copy_tree_stats_checked(
    root: Path,
    *,
    cancelled: CancelChecker,
    skip_noise: bool,
    noise_names_at_root_only: bool = False,
) -> tuple[int, int]:
    files = total = 0
    stack = [(root, 0)]
    while stack:
        directory, depth = stack.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError as exc:
            raise LegacyImportError("LEGACY_COPY_FAILED", "staging") from exc
        for entry in entries:
            if cancelled():
                raise LegacyImportError("LEGACY_IMPORT_CANCELLED", "staging")
            name = entry.name.casefold()
            if skip_noise and (
                name.endswith(_SKIP_SUFFIXES)
                or (
                    name in _SKIP_NAMES
                    and (not noise_names_at_root_only or depth == 0)
                )
            ):
                continue
            path = Path(entry.path)
            if entry.is_symlink() or is_link_or_junction(path):
                raise LegacyImportError("LEGACY_NESTED_LINK_UNSUPPORTED", "staging")
            if entry.is_dir(follow_symlinks=False):
                stack.append((path, depth + 1))
            elif entry.is_file(follow_symlinks=False):
                files += 1
                try:
                    total += entry.stat(follow_symlinks=False).st_size
                except OSError as exc:
                    raise LegacyImportError("LEGACY_COPY_FAILED", "staging") from exc
    return files, total


def _stop_copy_process(process: subprocess.Popen[bytes]) -> None:
    try:
        process.terminate()
        process.wait(timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            pass
