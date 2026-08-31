from __future__ import annotations

import errno
import os
import platform
import re
import shutil
from pathlib import Path

import yaml

from .files import is_link_or_junction, tree_stats
from .models import DomainInspection, LegacyInspection


_VERSION = re.compile(r"(?i)(?:v)?(0\.9(?:\.\d+)?(?:[-+][A-Za-z0-9_.-]+)?)")
_KNOWN_TTS_CHILDREN = {
    "cpu",
    "gpt",
    "g50",
    "gpt_sovits_macos",
    "_dl",
    "onnx",
    "GPT-SoVITS",
    "GPT_SoVITS",
}
_TARGET_PLATFORMS = {"Windows": "windows", "Darwin": "macos"}


def inspect_installation(source: Path, target: Path) -> LegacyInspection:
    source = Path(source).resolve(strict=False)
    target = Path(target).resolve(strict=False)
    blockers: list[dict[str, object]] = []
    warnings: list[dict[str, object]] = []

    if not source.is_dir():
        blockers.append({"code": "LEGACY_SOURCE_NOT_DIRECTORY", "stage": "inspect"})
    required = [source / "data" / "config", source / "data" / "chat_history"]
    if not all(path.is_dir() for path in required):
        blockers.append({"code": "LEGACY_LAYOUT_UNRECOGNIZED", "stage": "inspect"})
    if legacy_source_is_active(source):
        blockers.append({"code": "LEGACY_SOURCE_ACTIVE", "stage": "inspect"})
    source_platform = detect_legacy_source_platform(source)
    target_platform = _TARGET_PLATFORMS.get(platform.system(), "unknown")
    if source_platform == "unknown":
        blockers.append({"code": "LEGACY_PLATFORM_UNSUPPORTED", "stage": "inspect"})
    if target_platform == "unknown":
        blockers.append({"code": "LEGACY_TARGET_PLATFORM_UNSUPPORTED", "stage": "inspect"})
    elif source_platform != "unknown" and source_platform != target_platform:
        blockers.append({"code": "LEGACY_CROSS_PLATFORM_UNSUPPORTED", "stage": "inspect"})

    try:
        source.relative_to(target)
    except ValueError:
        pass
    else:
        blockers.append({"code": "LEGACY_SOURCE_TARGET_OVERLAP", "stage": "inspect"})
    try:
        target.relative_to(source)
    except ValueError:
        pass
    else:
        blockers.append({"code": "LEGACY_SOURCE_TARGET_OVERLAP", "stage": "inspect"})

    target_issue = target_semantic_empty_error(target)
    if target_issue:
        blockers.append({"code": target_issue, "stage": "inspect"})
    overwrite_domains = _target_overwrite_domains(target)
    if "__UNSAFE_LINK__" in overwrite_domains:
        blockers.append(
            {"code": "LEGACY_COMMIT_TARGET_LINK_UNSUPPORTED", "stage": "inspect"}
        )
        overwrite_domains = tuple(
            domain for domain in overwrite_domains if domain != "__UNSAFE_LINK__"
        )

    version = _detect_version(source)
    if not version.startswith("0.9"):
        blockers.append({"code": "LEGACY_VERSION_UNSUPPORTED", "stage": "inspect"})

    domains: dict[str, DomainInspection] = {}
    domains["config"] = _domain(source / "data" / "config")
    domains["characters"] = _domain(
        source / "characters", items=_count_manifests(source / "characters")
    )
    domains["history"] = _domain(
        source / "data" / "chat_history",
        items=_count_lines(source / "data" / "chat_history", ("*.jsonl", "*.archive")),
    )
    domains["memory"] = _domain(source / "data" / "memory")
    domains["notes"] = _domain(source / "data" / "notes")
    domains["reminders"] = _domain_file(source / "data" / "reminders.json")
    domains["tasks"] = _domain_file(source / "data" / "tasks.json")
    domains["characterStudio"] = _domain(source / "data" / "character_studio")
    domains["pluginData"] = _domain(source / "data" / "plugins")
    domains["legacyPlugins"] = _domain(source / "plugins")
    domains["screenState"] = _domain_file(source / "data" / "screen_awareness_state.json")
    domains["visualRecords"] = _domain(source / "data" / "visual_observations")
    domains["runtimeEvents"] = _domain(source / "data" / "runtime_events")
    domains["legacyMemoryJson"] = _domain_file(source / "data" / "memory.json")

    tts_root = legacy_tts_root(source)
    tts_external = is_link_or_junction(tts_root) if os.path.lexists(tts_root) else False
    resolved_tts = tts_root
    if tts_external:
        try:
            resolved_tts = tts_root.resolve(strict=True)
        except OSError:
            warnings.append({"code": "LEGACY_TTS_LINK_BROKEN", "stage": "inspect"})
        else:
            warnings.append({"code": "LEGACY_TTS_EXTERNAL_COPY", "stage": "inspect"})
            if _overlaps(resolved_tts, target):
                warnings.append({"code": "LEGACY_TTS_TARGET_OVERLAP", "stage": "inspect"})
    if resolved_tts.is_dir() and not _known_tts_layout(resolved_tts):
        warnings.append({"code": "LEGACY_TTS_LAYOUT_UNRECOGNIZED", "stage": "inspect"})
    domains["tts"] = _domain(resolved_tts, follow_root_link=tts_external)
    # The installed bundle tree is the macOS TTS root and is already counted
    # above. Only legacy ONNX resources are copied through the second path.
    bundles = _domain(source / "data" / "tts_bundles" / "onnx")
    domains["ttsBundles"] = bundles

    # History is preserved verbatim in quarantine and also materialized as an
    # indexed Timeline SQLite database.  Reserve twice its source size for the
    # converted database and indexes rather than starting a copy that cannot
    # reach the atomic commit.
    # Character packages and TTS runtimes are replaceable optional domains.
    # Their size must not prevent an import that can still preserve Timeline
    # and Memory; an optional copy can fail independently during staging.
    optional_domains = {"characters", "tts", "ttsBundles"}
    required_bytes = sum(
        value.bytes for name, value in domains.items() if name not in optional_domains
    ) + 2 * domains["history"].bytes
    try:
        available_bytes = shutil.disk_usage(target if target.exists() else target.parent).free
    except OSError:
        available_bytes = 0
        blockers.append({"code": "LEGACY_TARGET_SPACE_UNAVAILABLE", "stage": "inspect"})
    # Keep headroom for SQLite/journals and avoid beginning a copy that cannot commit.
    if available_bytes and available_bytes < required_bytes + max(64 * 1024 * 1024, required_bytes // 20):
        blockers.append({"code": "LEGACY_TARGET_SPACE_INSUFFICIENT", "stage": "inspect"})

    return LegacyInspection(
        schema_version=1,
        compatible=not blockers,
        detected_version=version,
        source_platform=source_platform,
        source_label=source.name[:120],
        tts_external_link=tts_external,
        required_bytes=required_bytes,
        available_bytes=available_bytes,
        domains=domains,
        overwrite_domains=overwrite_domains,
        blockers=tuple(blockers),
        warnings=tuple(warnings),
    )


def detect_legacy_version(source: Path) -> str:
    """Return the recognized 0.9.x version without applying platform policy."""

    return _detect_version(Path(source))


def legacy_source_is_active(source: Path) -> bool:
    """Return true only when the 0.9.x QLockFile PID is provably alive."""

    lock = Path(source) / "data" / "sakura.lock"
    try:
        first_line = lock.read_bytes()[:1024].splitlines()[0]
        pid = int(first_line.decode("ascii", errors="strict").strip())
    except (OSError, UnicodeError, ValueError, IndexError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        return _windows_process_is_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OverflowError:
        return False
    except OSError as exc:
        return exc.errno == errno.EPERM
    return True


def _windows_process_is_alive(pid: int) -> bool:
    try:
        import ctypes

        process = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not process:
            return ctypes.windll.kernel32.GetLastError() == 5
        try:
            exit_code = ctypes.c_ulong()
            if not ctypes.windll.kernel32.GetExitCodeProcess(
                process, ctypes.byref(exit_code)
            ):
                return False
            return exit_code.value == 259
        finally:
            ctypes.windll.kernel32.CloseHandle(process)
    except (AttributeError, OSError):
        return False


def detect_legacy_source_platform(source: Path) -> str:
    """Identify a packaged 0.9.x source from platform-specific runtime files."""

    source = Path(source)
    windows_runtime = (source / "runtime" / "python.exe").is_file()
    macos_runtime = any(
        (source / "runtime" / "bin" / name).is_file()
        for name in ("python", "python3")
    )
    if windows_runtime != macos_runtime:
        return "windows" if windows_runtime else "macos"

    windows_launcher = (source / "start.bat").is_file()
    macos_launcher = (source / "scripts" / "start.command").is_file()
    if windows_launcher != macos_launcher:
        return "windows" if windows_launcher else "macos"
    return "unknown"


def legacy_tts_root(source: Path) -> Path:
    """Return the 0.9.x TTS tree that maps to the Runtime v2 ``tts`` root."""

    source = Path(source)
    installed = source / "data" / "tts_bundles" / "installed"
    if (
        detect_legacy_source_platform(source) == "macos"
        and os.path.lexists(installed)
    ):
        return installed
    direct = source / "tts"
    if os.path.lexists(direct):
        return direct
    if os.path.lexists(installed):
        return installed
    return direct


def target_semantic_empty_error(target: Path) -> str | None:
    """Return only target conditions that make a transactional import unsafe.

    Existing v2 user data is allowed.  The commit journal backs up every file
    it replaces, so a retry can overwrite stale/partially initialized targets
    and still restore them if Core validation fails.
    """

    if not target.exists():
        return None
    if not target.is_dir():
        return "LEGACY_TARGET_NOT_DIRECTORY"
    try:
        if any(child.name.startswith(".legacy-import-") for child in target.iterdir()):
            return "LEGACY_IMPORT_RECOVERY_REQUIRED"
    except OSError:
        return "LEGACY_TARGET_NOT_DIRECTORY"
    return None


def _target_overwrite_domains(target: Path) -> tuple[str, ...]:
    paths = {
        "配置": (Path("config"),),
        "聊天历史": (Path("data/chat_history"),),
        "长期记忆": (Path("data/memory"),),
        "角色": (Path("characters"),),
        "TTS": (Path("tts"),),
        "插件数据": (Path("data/plugins"), Path("plugins/user")),
        "其他用户数据": (
            Path("data/notes"),
            Path("data/reminders.json"),
            Path("data/tasks.json"),
            Path("data/character_studio"),
        ),
    }
    result: list[str] = []
    for label, relatives in paths.items():
        present = False
        for relative in relatives:
            path = target / relative
            if os.path.lexists(path) and is_link_or_junction(path):
                # The transaction layer rechecks immediately before every
                # rename. Inspector rejects early so confirmation can never be
                # mistaken for authority to escape the user root.
                return ("__UNSAFE_LINK__",)
            if path.is_file():
                present = True
            elif path.is_dir():
                try:
                    present = any(child.is_file() for child in path.rglob("*"))
                except OSError:
                    present = True
            if present:
                break
        if present:
            result.append(label)
    return tuple(result)


def _detect_version(source: Path) -> str:
    structural = _detect_structural_version(source)
    if structural:
        return structural
    candidates: list[str] = []
    version_file = source / "VERSION"
    if version_file.is_file():
        try:
            candidates.append(version_file.read_text(encoding="utf-8", errors="replace")[:256])
        except OSError:
            pass
    candidates.append(source.name)
    for candidate in candidates:
        match = _VERSION.search(candidate)
        if match:
            return match.group(1).lower().removeprefix("v")
    # 0.9 layout revisions did not always ship trustworthy VERSION text.
    if (source / "data" / "config" / "system_config.yaml").is_file():
        return "0.9.x"
    return "unknown"


def _detect_structural_version(source: Path) -> str:
    config_root = source / "data" / "config"
    try:
        system = yaml.safe_load((config_root / "system_config.yaml").read_text(encoding="utf-8"))
        api = yaml.safe_load((config_root / "api.yaml").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError):
        return ""
    if not isinstance(system, dict) or not isinstance(api, dict):
        return ""
    raw_version = system.get("config_version")
    if raw_version is None:
        return "0.9.6"
    try:
        config_version = int(raw_version)
    except (TypeError, ValueError):
        return ""
    if config_version >= 4:
        return "0.9.9"
    if config_version == 3:
        tts = api.get("tts")
        provider = str(tts.get("provider") or "").casefold() if isinstance(tts, dict) else ""
        if isinstance(api.get("api_profiles"), list) or "genie" in provider:
            return "0.9.8"
        return "0.9.7"
    return ""


def _domain(path: Path, *, items: int = 0, follow_root_link: bool = False) -> DomainInspection:
    files, size = tree_stats(path, follow_root_link=follow_root_link)
    return DomainInspection(present=path.exists(), files=files, bytes=size, items=items)


def _domain_file(path: Path) -> DomainInspection:
    if not path.is_file():
        return DomainInspection()
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    return DomainInspection(present=True, files=1, bytes=size, items=1)


def _count_manifests(root: Path) -> int:
    return sum(1 for _ in root.glob("*/character.json")) if root.is_dir() else 0


def _count_lines(root: Path, patterns: tuple[str, ...]) -> int:
    if not root.is_dir():
        return 0
    count = 0
    for pattern in patterns:
        for path in root.glob(pattern):
            try:
                with path.open("rb") as handle:
                    count += sum(1 for line in handle if line.strip())
            except OSError:
                continue
    return count


def _known_tts_layout(root: Path) -> bool:
    try:
        children = {child.name for child in root.iterdir()}
    except OSError:
        return False
    return not children or bool(children & _KNOWN_TTS_CHILDREN)


def _overlaps(left: Path, right: Path) -> bool:
    left = left.resolve(strict=False)
    right = right.resolve(strict=False)
    try:
        left.relative_to(right)
        return True
    except ValueError:
        pass
    try:
        right.relative_to(left)
        return True
    except ValueError:
        return False
