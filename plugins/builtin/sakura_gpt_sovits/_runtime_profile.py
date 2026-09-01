"""Generate and validate the managed GPT-SoVITS inference profile.

The host-side entry point launches this file with the bundled GPT-SoVITS
Python.  Device detection therefore observes the exact torch build used by
api_v2.py instead of the Sakura Core runtime.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any, Callable, Iterable, Mapping, Optional


PROFILE_FILENAME = "tts_infer_sakura_managed.yaml"
_RESULT_PREFIX = "SAKURA_GPT_SOVITS_PROFILE="
_MIN_VRAM_GIB = 6.0
_VRAM_TOLERANCE_GIB = 0.25
_ERROR_CODES = {
    "TTS_ACCELERATOR_UNAVAILABLE",
    "TTS_DEVICE_PROBE_FAILED",
    "TTS_PROFILE_GENERATION_FAILED",
    "TTS_RUNTIME_PATH_REPAIR_FAILED",
}
_MANAGED_PATH_FIELDS = (
    "bert_base_path",
    "cnhuhbert_base_path",
    "t2s_weights_path",
    "vits_weights_path",
)
_MANAGED_IMPORT_PATHS = (
    Path("."),
    Path("GPT_SoVITS/BigVGAN"),
    Path("tools"),
    Path("tools/asr"),
    Path("GPT_SoVITS"),
    Path("tools/uvr5"),
)
_REQUIRED_IMPORT_PATHS = (Path("tools"), Path("GPT_SoVITS"))


class RuntimeProfileError(RuntimeError):
    """Stable managed-runtime configuration failure."""


@dataclass(frozen=True)
class DeviceCandidate:
    index: int
    name: str
    capability: tuple[int, int]
    total_memory_gib: float
    free_memory_gib: float
    fp16_works: bool


@dataclass(frozen=True)
class DeviceProfile:
    device: str
    is_half: bool
    device_name: str


def managed_profile_path(work_dir: Path) -> Path:
    return Path(work_dir) / "GPT_SoVITS" / "configs" / PROFILE_FILENAME


def _native_windows_path(value: object) -> str:
    text = str(value)
    if sys.platform == "win32":
        if text.startswith("\\\\?\\UNC\\"):
            text = "\\\\" + text[8:]
        elif text.startswith("\\\\?\\"):
            text = text[4:]
    return os.path.normpath(text)


def _path_identity(value: Path) -> str:
    return os.path.normcase(_native_windows_path(Path(value).resolve(strict=False)))


def is_managed_profile(path: Path, work_dir: Path) -> bool:
    return _path_identity(Path(path)) == _path_identity(managed_profile_path(work_dir))


def find_runtime_python(work_dir: Path) -> Optional[Path]:
    runtime_dir = Path(work_dir) / "runtime"
    names = ("python.exe", "python") if sys.platform == "win32" else (
        "bin/python3",
        "bin/python",
        "python3",
        "python",
    )
    for name in names:
        candidate = runtime_dir / name
        if candidate.is_file():
            return candidate
    return None


def repair_managed_runtime_paths(
    work_dir: Path,
    *,
    platform: Optional[str] = None,
) -> bool:
    """Make the bundled Python import paths survive moving the runtime.

    The Windows GPT-SoVITS archives use an isolated embedded Python whose
    ``users.pth`` is the only route to ``tools`` and ``GPT_SoVITS``.  Some
    installed archives contain absolute paths from their previous location.
    Replace only recognized bundle entries with relative paths, preserving
    comments, import hooks, and unknown custom entries verbatim.
    """

    if (platform or sys.platform) != "win32":
        return False
    work_dir = Path(work_dir).resolve(strict=False)
    site_packages = work_dir / "runtime" / "Lib" / "site-packages"
    users_pth = site_packages / "users.pth"
    if not users_pth.is_file():
        return False
    if any(not (work_dir / relative).is_dir() for relative in _REQUIRED_IMPORT_PATHS):
        raise RuntimeProfileError("TTS_RUNTIME_PATH_REPAIR_FAILED")
    try:
        raw = users_pth.read_text(encoding="utf-8")
        expected = [
            (work_dir / relative).resolve(strict=False)
            for relative in _MANAGED_IMPORT_PATHS
            if (work_dir / relative).is_dir()
        ]
        portable = [
            os.path.relpath(target, site_packages).replace("\\", "/")
            for target in expected
        ]
        original_lines = raw.splitlines()
        old_roots = _managed_import_roots(original_lines)
        kept: list[str] = []
        insertion = None
        for line in original_lines:
            if _is_managed_import_entry(line, site_packages, expected, old_roots):
                if insertion is None:
                    insertion = len(kept)
                continue
            kept.append(line)
        if insertion is None:
            insertion = len(kept)
        updated_lines = [*kept[:insertion], *portable, *kept[insertion:]]
        encoded = "\n".join(updated_lines) + "\n"
        if encoded == raw:
            return False
        temporary: Optional[Path] = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                prefix=".users.",
                suffix=".tmp.pth",
                dir=str(site_packages),
                delete=False,
            ) as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
                temporary = Path(handle.name)
            os.replace(temporary, users_pth)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    except RuntimeProfileError:
        raise
    except (OSError, UnicodeError, ValueError) as error:
        raise RuntimeProfileError("TTS_RUNTIME_PATH_REPAIR_FAILED") from error
    return True


def _is_managed_import_entry(
    line: str,
    site_packages: Path,
    expected: list[Path],
    old_roots: set[tuple[str, ...]],
) -> bool:
    value = line.strip()
    if not value or value.startswith("#") or value.startswith(("import ", "import\t")):
        return False
    candidate = (site_packages / value).resolve(strict=False)
    if any(_path_identity(candidate) == _path_identity(target) for target in expected):
        return True
    windows = PureWindowsPath(value.replace("/", "\\"))
    if not windows.is_absolute():
        return False
    parts = tuple(part.casefold() for part in windows.parts)
    if parts in old_roots:
        return True
    for relative in _MANAGED_IMPORT_PATHS[1:]:
        suffix = tuple(part.casefold() for part in relative.parts)
        if len(parts) > len(suffix) and parts[-len(suffix) :] == suffix:
            return True
    return False


def _managed_import_roots(lines: Iterable[str]) -> set[tuple[str, ...]]:
    roots: set[tuple[str, ...]] = set()
    for line in lines:
        value = line.strip()
        if not value or value.startswith("#") or value.startswith(("import ", "import\t")):
            continue
        windows = PureWindowsPath(value.replace("/", "\\"))
        if not windows.is_absolute():
            continue
        parts = tuple(part.casefold() for part in windows.parts)
        for relative in _MANAGED_IMPORT_PATHS[1:]:
            suffix = tuple(part.casefold() for part in relative.parts)
            if len(parts) <= len(suffix) or parts[-len(suffix) :] != suffix:
                continue
            roots.add(parts[: -len(suffix)])
    return roots


def select_device_profile(
    candidates: Iterable[DeviceCandidate],
    *,
    require_cuda: bool,
) -> DeviceProfile:
    compatible = [
        candidate
        for candidate in candidates
        if candidate.total_memory_gib >= _MIN_VRAM_GIB - _VRAM_TOLERANCE_GIB
    ]
    if not compatible:
        if require_cuda:
            raise RuntimeProfileError("TTS_ACCELERATOR_UNAVAILABLE")
        return DeviceProfile("cpu", False, "CPU")
    selected = max(
        compatible,
        key=lambda item: (
            item.capability[0],
            item.capability[1],
            item.free_memory_gib,
            item.total_memory_gib,
            -item.index,
        ),
    )
    major, minor = selected.capability
    sm_version = major + minor / 10.0
    is_gtx_16 = bool(re.search(r"\bGTX\s*16\d{2}\b", selected.name, re.IGNORECASE))
    use_half = sm_version > 6.1 and not is_gtx_16 and selected.fp16_works
    return DeviceProfile(f"cuda:{selected.index}", use_half, selected.name)


def prepare_managed_profile(
    work_dir: Path,
    *,
    runtime_python: Optional[Path] = None,
    configured_path: Optional[Path] = None,
    require_cuda: bool = False,
    platform: Optional[str] = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> Optional[Path]:
    """Return an explicit config path, generating Sakura's profile on Windows.

    An arbitrary user-supplied config remains authoritative.  Only an empty
    path or Sakura's own fixed profile is regenerated.
    """

    platform = platform or sys.platform
    work_dir = Path(work_dir)
    configured = Path(configured_path) if configured_path is not None else None
    if platform != "win32":
        return configured
    repair_managed_runtime_paths(work_dir, platform=platform)
    if configured is not None and not is_managed_profile(configured, work_dir):
        return configured
    python = Path(runtime_python) if runtime_python is not None else find_runtime_python(work_dir)
    if python is None or not python.is_file():
        raise RuntimeProfileError("TTS_DEVICE_PROBE_FAILED")
    command = [
        _native_windows_path(python),
        _native_windows_path(Path(__file__).resolve()),
        "--worker",
        _native_windows_path(work_dir.resolve(strict=False)),
    ]
    if require_cuda:
        command.append("--require-cuda")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        completed = runner(
            command,
            cwd=_native_windows_path(work_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=90,
            env=env,
            creationflags=creationflags,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeProfileError("TTS_DEVICE_PROBE_FAILED") from error
    payload: Optional[Mapping[str, Any]] = None
    for line in reversed(completed.stdout.splitlines()):
        if line.startswith(_RESULT_PREFIX):
            try:
                value = json.loads(line[len(_RESULT_PREFIX) :])
            except ValueError:
                break
            if isinstance(value, Mapping):
                payload = value
            break
    if payload is None:
        raise RuntimeProfileError("TTS_DEVICE_PROBE_FAILED")
    if not payload.get("ok"):
        code = str(payload.get("code") or "TTS_DEVICE_PROBE_FAILED")
        raise RuntimeProfileError(code if code in _ERROR_CODES else "TTS_DEVICE_PROBE_FAILED")
    result_path = payload.get("path")
    if completed.returncode != 0 or not isinstance(result_path, str):
        raise RuntimeProfileError("TTS_PROFILE_GENERATION_FAILED")
    result = Path(result_path)
    if not result.is_file() or not is_managed_profile(result, work_dir):
        raise RuntimeProfileError("TTS_PROFILE_GENERATION_FAILED")
    return result.resolve()


def _probe_candidates() -> list[DeviceCandidate]:
    import torch

    if not torch.cuda.is_available():
        return []
    candidates: list[DeviceCandidate] = []
    for index in range(torch.cuda.device_count()):
        properties = torch.cuda.get_device_properties(index)
        total_gib = float(properties.total_memory) / (1024**3)
        try:
            free_bytes, _total_bytes = torch.cuda.mem_get_info(index)
            free_gib = float(free_bytes) / (1024**3)
        except Exception:
            free_gib = total_gib
        capability = tuple(int(value) for value in torch.cuda.get_device_capability(index))
        name = str(torch.cuda.get_device_name(index))
        sm_version = capability[0] + capability[1] / 10.0
        should_try_half = sm_version > 6.1 and not re.search(r"\bGTX\s*16\d{2}\b", name, re.IGNORECASE)
        fp16_works = False
        if should_try_half:
            try:
                device = torch.device(f"cuda:{index}")
                tensor = torch.ones((32, 32), device=device, dtype=torch.float16)
                result = tensor @ tensor
                torch.cuda.synchronize(index)
                fp16_works = bool(result.is_cuda and result.dtype == torch.float16)
                del tensor, result
            except Exception:
                fp16_works = False
        candidates.append(
            DeviceCandidate(
                index=index,
                name=name,
                capability=(capability[0], capability[1]),
                total_memory_gib=total_gib,
                free_memory_gib=free_gib,
                fp16_works=fp16_works,
            )
        )
    return candidates


def _profile_requires_cuda(payload: Mapping[str, Any]) -> bool:
    custom = payload.get("custom")
    return isinstance(custom, Mapping) and str(custom.get("device", "")).lower().startswith("cuda")


def _update_profile_payload(payload: Mapping[str, Any], profile: DeviceProfile) -> dict[str, Any]:
    updated = dict(payload)
    changed = 0
    for key, value in payload.items():
        if not isinstance(value, Mapping):
            continue
        if not ({"device", "is_half", "version"} & set(value)):
            continue
        section = dict(value)
        section["device"] = profile.device
        section["is_half"] = profile.is_half
        updated[str(key)] = section
        changed += 1
    if changed == 0:
        raise RuntimeProfileError("TTS_PROFILE_GENERATION_FAILED")
    return _reset_managed_custom_paths(updated)


def _reset_managed_custom_paths(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Keep Sakura's generated profile independent from a previous install.

    The service starts with the bundled weights from the selected version and
    the Provider switches to character weights after the HTTP endpoint is
    ready.  A generated ``custom`` section may otherwise retain absolute
    character paths written by an older Sakura installation.
    """

    updated = dict(payload)
    raw_custom = payload.get("custom")
    if not isinstance(raw_custom, Mapping):
        return updated
    custom = dict(raw_custom)
    version = str(custom.get("version") or "").strip()
    defaults = payload.get(version)
    defaults = defaults if isinstance(defaults, Mapping) else {}
    for field in _MANAGED_PATH_FIELDS:
        current = custom.get(field)
        if not _absolute_runtime_path(current):
            continue
        replacement = defaults.get(field)
        if not isinstance(replacement, str) or not replacement.strip():
            raise RuntimeProfileError("TTS_PROFILE_GENERATION_FAILED")
        custom[field] = replacement
    updated["custom"] = custom
    return updated


def _absolute_runtime_path(value: object) -> bool:
    text = str(value or "").strip()
    return bool(text) and (Path(text).is_absolute() or PureWindowsPath(text).is_absolute())


def _validate_tts_config(work_dir: Path, path: Path, profile: DeviceProfile) -> None:
    """Validate the generated YAML without importing or loading TTS models."""

    del work_dir
    import yaml

    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as error:
        raise RuntimeProfileError("TTS_PROFILE_GENERATION_FAILED") from error
    if not isinstance(payload, Mapping):
        raise RuntimeProfileError("TTS_PROFILE_GENERATION_FAILED")
    sections = [
        value
        for value in payload.values()
        if isinstance(value, Mapping) and ({"device", "is_half", "version"} & set(value))
    ]
    if not sections or any(
        str(section.get("device")) != profile.device
        or section.get("is_half") is not profile.is_half
        for section in sections
    ):
        raise RuntimeProfileError("TTS_PROFILE_GENERATION_FAILED")


def _generate_profile(work_dir: Path, *, require_cuda: bool) -> tuple[Path, DeviceProfile]:
    import yaml

    target = managed_profile_path(work_dir)
    source = target if target.is_file() else target.with_name("tts_infer.yaml")
    try:
        payload = yaml.safe_load(source.read_text(encoding="utf-8"))
    except Exception as error:
        raise RuntimeProfileError("TTS_PROFILE_GENERATION_FAILED") from error
    if not isinstance(payload, Mapping):
        raise RuntimeProfileError("TTS_PROFILE_GENERATION_FAILED")
    profile = select_device_profile(
        _probe_candidates(),
        require_cuda=require_cuda or (source == target and _profile_requires_cuda(payload)),
    )
    updated = _update_profile_payload(payload, profile)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{target.stem}.",
            suffix=".tmp.yaml",
            dir=str(target.parent),
            delete=False,
        ) as handle:
            yaml.safe_dump(updated, handle, allow_unicode=True, sort_keys=False)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        _validate_tts_config(work_dir, temporary, profile)
        os.replace(temporary, target)
        temporary = None
    except RuntimeProfileError:
        raise
    except Exception as error:
        raise RuntimeProfileError("TTS_PROFILE_GENERATION_FAILED") from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return target.resolve(), profile


def _worker(work_dir: Path, *, require_cuda: bool) -> int:
    try:
        path, profile = _generate_profile(work_dir, require_cuda=require_cuda)
        payload = {
            "ok": True,
            "path": str(path),
            "device": profile.device,
            "isHalf": profile.is_half,
            "deviceName": profile.device_name,
        }
        code = 0
    except RuntimeProfileError as error:
        failure = str(error)
        payload = {"ok": False, "code": failure if failure in _ERROR_CODES else "TTS_PROFILE_GENERATION_FAILED"}
        code = 2
    except Exception:
        payload = {"ok": False, "code": "TTS_DEVICE_PROBE_FAILED"}
        code = 2
    print(f"{_RESULT_PREFIX}{json.dumps(payload, ensure_ascii=False)}", flush=True)
    return code


def _main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", type=Path)
    parser.add_argument("--require-cuda", action="store_true")
    args = parser.parse_args()
    if args.worker is None:
        return 2
    return _worker(args.worker, require_cuda=args.require_cuda)


if __name__ == "__main__":
    raise SystemExit(_main())
