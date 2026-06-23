from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlparse
from urllib.request import url2pathname

from app.config.settings_service import AppSettingsService
from app.sensory.audio_deployment import (
    build_llama_cpp_audio_prepare_requirement,
    prepare_llama_cpp_audio_backend,
)
from app.sensory.audio_models import recommended_llama_cpp_audio_model
from app.sensory.audio_smoke import (
    SensoryAudioSmokePlan,
    build_sensory_audio_smoke_plan,
    run_sensory_audio_smoke_test,
)
from app.sensory.audio_runtime_doctor import build_sensory_audio_runtime_doctor_report
from app.sensory.llama_cpp_runtime import (
    DEFAULT_LLAMA_CPP_MANAGED_PORT,
    LLAMA_CPP_MANAGED_RUNTIME_MARKER,
    LLAMA_CPP_GITHUB_LATEST_RELEASE_API,
    discover_llama_server_binary,
    fetch_llama_cpp_runtime_package_catalog,
    fetch_latest_llama_cpp_runtime_packages,
    install_llama_cpp_runtime_package,
    llama_cpp_platform_key,
    llama_cpp_runtime_packages_from_manifest,
    select_llama_cpp_runtime_package,
)
from app.sensory.models import SensoryProviderMode, SensorySource, coerce_sensory_source
from app.sensory.settings import SensoryProviderConfig


class SensoryAudioRuntimeCliError(RuntimeError):
    """Raised for expected CLI validation failures."""


_KNOWN_LLAMA_CPP_PLATFORM_KEYS = (
    "linux-arm64",
    "linux-x64",
    "macos-arm64",
    "macos-x64",
    "windows-arm64",
    "windows-x64",
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "install-runtime":
            return _run_install_runtime(args)
        if args.command == "runtime-manifest":
            return _run_runtime_manifest(args)
        if args.command == "runtime-manifest-check":
            return _run_runtime_manifest_check(args)
        if args.command == "doctor":
            return _run_doctor(args)
        if args.command == "prepare-backend":
            return _run_prepare_backend(args)
        if args.command == "smoke":
            return _run_smoke(args)
        return _run_plan(args)
    except SensoryAudioRuntimeCliError as exc:
        _print_payload({"ok": False, "message": str(exc)}, pretty=bool(args.pretty))
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.sensory.audio_runtime_cli",
        description="Verify Sakura's optional local sensory audio inference runtime.",
    )
    parser.add_argument("--base-dir", type=Path, default=Path.cwd(), help="Sakura checkout/runtime root.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")

    subparsers = parser.add_subparsers(dest="command")
    plan = subparsers.add_parser("plan", help="Dry-run audio provider/runtime readiness.")
    _add_pretty_arg(plan)
    _add_provider_args(plan)

    smoke = subparsers.add_parser("smoke", help="Run a short generated-WAV provider smoke test.")
    _add_pretty_arg(smoke)
    _add_provider_args(smoke)
    smoke.add_argument(
        "--allow-model-download",
        action="store_true",
        help="Allow managed llama.cpp to download remote GGUF models during smoke.",
    )

    install = subparsers.add_parser("install-runtime", help="Install the official llama.cpp runtime.")
    _add_pretty_arg(install)
    install.add_argument(
        "--yes",
        action="store_true",
        help="Download/install the selected official runtime if no llama-server is already available.",
    )
    install.add_argument(
        "--preferred-variant",
        default="auto",
        help="Runtime variant preference, usually auto/cpu/metal.",
    )

    prepare_backend = subparsers.add_parser(
        "prepare-backend",
        help="Prepare managed llama.cpp runtime and the recommended local audio model cache.",
    )
    _add_pretty_arg(prepare_backend)
    prepare_backend.add_argument(
        "--source",
        choices=[SensorySource.SPEECH.value, SensorySource.SOUND.value],
        default=SensorySource.SPEECH.value,
        help="Audio sensory source to prepare.",
    )
    prepare_backend.add_argument(
        "--yes",
        action="store_true",
        help="Allow downloading the runtime package and recommended GGUF model files.",
    )

    manifest = subparsers.add_parser(
        "runtime-manifest",
        help="Generate a llama.cpp runtime manifest template without downloading archives.",
    )
    _add_pretty_arg(manifest)
    manifest.add_argument(
        "--mirror-base-url",
        default="",
        help="Rewrite package URLs to this mirror base URL using the original archive filenames.",
    )
    manifest.add_argument(
        "--relative-archive-dir",
        default="",
        help="Rewrite package URLs to a relative archive directory, for example archives.",
    )
    manifest.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional output JSON path. Omit to print to stdout only.",
    )
    manifest.add_argument(
        "--archive-root",
        type=Path,
        default=None,
        help="Optional local archive directory. Adds sha256 and size_bytes for matching archive filenames.",
    )

    manifest_check = subparsers.add_parser(
        "runtime-manifest-check",
        help="Validate a llama.cpp runtime manifest without installing or downloading.",
    )
    _add_pretty_arg(manifest_check)
    manifest_check.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="Manifest path. Defaults to data/local_runtimes/llama_cpp/runtime_manifest.json.",
    )
    manifest_check.add_argument(
        "--archive-root",
        type=Path,
        default=None,
        help="Optional local archive directory for validating package files by filename.",
    )
    manifest_check.add_argument(
        "--require-platform",
        action="append",
        default=[],
        help="Platform key that must be present. Can be repeated.",
    )
    manifest_check.add_argument(
        "--require-known-platforms",
        action="store_true",
        help="Require all built-in platform keys to be present.",
    )

    doctor = subparsers.add_parser(
        "doctor",
        help="Summarize audio runtime readiness without installing or downloading.",
    )
    _add_pretty_arg(doctor)

    parser.set_defaults(
        command="plan",
        source=SensorySource.SPEECH.value,
        provider_id="",
        endpoint="",
        model="",
        backend="",
        managed_llama_defaults=False,
        llama_binary_path="",
    )
    return parser


def _add_pretty_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--pretty", action="store_true", help=argparse.SUPPRESS)


def _add_provider_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--source",
        choices=[SensorySource.SPEECH.value, SensorySource.SOUND.value],
        default=SensorySource.SPEECH.value,
        help="Audio sensory source to inspect.",
    )
    parser.add_argument("--provider-id", default="", help="Provider id from sensory.providers.")
    parser.add_argument("--endpoint", default="", help="Override provider endpoint.")
    parser.add_argument("--model", default="", help="Override provider model.")
    parser.add_argument("--backend", default="", help="Override provider backend extra value.")
    parser.add_argument(
        "--managed-llama-defaults",
        action="store_true",
        help="Use Sakura's managed local llama.cpp defaults instead of saved settings.",
    )
    parser.add_argument("--llama-binary-path", default="", help="Managed llama.cpp binary override.")


def _run_plan(args: argparse.Namespace) -> int:
    source = coerce_sensory_source(args.source)
    config = _provider_config_from_args(args, source)
    plan = build_sensory_audio_smoke_plan(config, base_dir=Path(args.base_dir), source=source)
    _print_payload(plan.to_mapping(), pretty=bool(args.pretty))
    return 0 if plan.ok else 1


def _run_smoke(args: argparse.Namespace) -> int:
    source = coerce_sensory_source(args.source)
    config = _provider_config_from_args(args, source)
    plan = build_sensory_audio_smoke_plan(config, base_dir=Path(args.base_dir), source=source)
    if not plan.ok:
        _print_payload(plan.to_mapping(), pretty=bool(args.pretty))
        return 1
    if _remote_managed_llama_model(plan) and not bool(args.allow_model_download):
        size = f"预计下载 {plan.model_download_hint}" if plan.model_download_hint else "可能下载 GGUF 模型"
        _print_payload(
            {
                "ok": False,
                "message": f"真实 smoke 会让 llama.cpp 拉取远端模型（{size}）。确认后重新运行并传入 --allow-model-download。",
                "plan": plan.to_mapping(),
            },
            pretty=bool(args.pretty),
        )
        return 2
    result = run_sensory_audio_smoke_test(config, base_dir=Path(args.base_dir), source=source)
    _print_payload(result.to_mapping(), pretty=bool(args.pretty))
    return 0 if result.ok else 1


def _run_install_runtime(args: argparse.Namespace) -> int:
    base_dir = Path(args.base_dir)
    existing = discover_llama_server_binary(base_dir)
    if existing:
        _print_payload(
            {
                "ok": True,
                "already_installed": True,
                "binary_path": existing,
                "platform_key": llama_cpp_platform_key(),
                "message": "已找到可用的 llama-server。",
            },
            pretty=bool(args.pretty),
        )
        return 0
    if not bool(args.yes):
        _print_payload(
            {
                "ok": False,
                "platform_key": llama_cpp_platform_key(),
                "message": "未找到 llama-server。该命令需要下载官方 llama.cpp 运行时；确认后重新运行并传入 --yes。",
            },
            pretty=bool(args.pretty),
        )
        return 2
    catalog = fetch_llama_cpp_runtime_package_catalog(base_dir=base_dir, timeout_seconds=30)
    package = select_llama_cpp_runtime_package(
        catalog.packages,
        preferred_variant=str(args.preferred_variant or "auto"),
    )
    result = install_llama_cpp_runtime_package(base_dir, package, timeout_seconds=600)
    payload = result.to_mapping()
    payload["ok"] = True
    payload["platform_key"] = llama_cpp_platform_key()
    payload["package_source"] = catalog.source
    _print_payload(payload, pretty=bool(args.pretty))
    return 0


def _run_runtime_manifest(args: argparse.Namespace) -> int:
    if args.mirror_base_url and args.relative_archive_dir:
        raise SensoryAudioRuntimeCliError("不能同时指定 --mirror-base-url 和 --relative-archive-dir。")
    packages = fetch_latest_llama_cpp_runtime_packages(timeout_seconds=30)
    entries = []
    archive_root = Path(args.archive_root).expanduser() if args.archive_root is not None else None
    for package in sorted(
        (package.normalized() for package in packages),
        key=lambda item: (item.platform_key, item.variant, item.package_id),
    ):
        data = package.to_mapping()
        filename = _url_filename(package.url)
        data["url"] = _runtime_manifest_url(
            package.url,
            mirror_base_url=str(args.mirror_base_url or ""),
            relative_archive_dir=str(args.relative_archive_dir or ""),
        )
        if archive_root is not None:
            _add_local_archive_metadata(data, archive_root / filename)
        entries.append(data)
    payload = {
        "manifest_version": 1,
        "source": LLAMA_CPP_GITHUB_LATEST_RELEASE_API,
        "packages": entries,
    }
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2 if args.pretty else None)
    output = args.output
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(f"{text}\n", encoding="utf-8")
    else:
        print(text)
    return 0


def _run_runtime_manifest_check(args: argparse.Namespace) -> int:
    manifest_path = _runtime_manifest_check_path(args)
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SensoryAudioRuntimeCliError(f"无法读取 runtime manifest：{manifest_path}：{exc}") from exc
    if not isinstance(payload, dict):
        raise SensoryAudioRuntimeCliError(f"runtime manifest 必须是 JSON 对象：{manifest_path}")
    packages = [package.normalized() for package in llama_cpp_runtime_packages_from_manifest(payload)]
    issues: list[str] = []
    if not packages:
        issues.append("manifest 未包含可用 packages")
    required_platforms = _required_manifest_platforms(args)
    package_platforms = {package.platform_key for package in packages}
    missing_platforms = [platform for platform in required_platforms if platform not in package_platforms]
    for platform in missing_platforms:
        issues.append(f"缺少平台包：{platform}")
    archive_root = Path(args.archive_root).expanduser() if args.archive_root is not None else None
    checked_packages = [
        _check_manifest_package_archive(package, manifest_path.parent, archive_root, issues)
        for package in packages
    ]
    result = {
        "ok": not issues,
        "manifest_path": str(manifest_path),
        "package_count": len(packages),
        "platforms": sorted(package_platforms),
        "required_platforms": required_platforms,
        "missing_platforms": missing_platforms,
        "issues": issues,
        "packages": checked_packages,
    }
    _print_payload(result, pretty=bool(args.pretty))
    return 0 if not issues else 1


def _run_doctor(args: argparse.Namespace) -> int:
    _print_payload(
        build_sensory_audio_runtime_doctor_report(Path(args.base_dir)),
        pretty=bool(args.pretty),
    )
    return 0


def _run_prepare_backend(args: argparse.Namespace) -> int:
    base_dir = Path(args.base_dir)
    source = coerce_sensory_source(args.source)
    report = build_sensory_audio_runtime_doctor_report(base_dir)
    requirement = build_llama_cpp_audio_prepare_requirement(report, source)
    if not bool(args.yes) and not bool(requirement.get("ok")):
        _print_payload(
            {
                "ok": False,
                "message": "准备 llama.cpp 音频后端需要下载运行时或推荐模型；确认后重新运行并传入 --yes。",
                "requirement": requirement,
                "doctor": report,
            },
            pretty=bool(args.pretty),
        )
        return 2
    payload = prepare_llama_cpp_audio_backend(
        base_dir,
        source,
        download_runtime=bool(args.yes),
        download_model=bool(args.yes),
    )
    _print_payload(payload, pretty=bool(args.pretty))
    return 0


def _provider_config_from_args(
    args: argparse.Namespace,
    source: SensorySource,
) -> SensoryProviderConfig:
    if bool(args.managed_llama_defaults):
        config = _managed_llama_default_config(args, source)
    else:
        config = _saved_provider_config(args, source)
    if args.endpoint or args.model or args.backend or args.llama_binary_path:
        extra = dict(config.extra)
        if args.backend:
            extra["backend"] = str(args.backend).strip()
        if args.llama_binary_path:
            extra["managed_runtime"] = LLAMA_CPP_MANAGED_RUNTIME_MARKER
            extra["llama_binary_path"] = str(args.llama_binary_path).strip()
        config = replace(
            config,
            endpoint=str(args.endpoint or config.endpoint).strip(),
            model=str(args.model or config.model).strip(),
            extra=extra,
        )
    return config.normalized()


def _saved_provider_config(args: argparse.Namespace, source: SensorySource) -> SensoryProviderConfig:
    settings = AppSettingsService(base_dir=Path(args.base_dir)).load_sensory_settings()
    provider_id = str(args.provider_id or "").strip()
    provider = settings.providers.get(provider_id) if provider_id else settings.provider_for_source(source)
    if provider is None:
        if provider_id:
            raise SensoryAudioRuntimeCliError(f"未找到感官 provider：{provider_id}")
        raise SensoryAudioRuntimeCliError(f"未配置 {source.value} 感官 provider。")
    return provider


def _managed_llama_default_config(
    args: argparse.Namespace,
    source: SensorySource,
) -> SensoryProviderConfig:
    recommendation = recommended_llama_cpp_audio_model(source)
    model = str(args.model or (recommendation.model if recommendation is not None else "")).strip()
    if not model:
        raise SensoryAudioRuntimeCliError(f"{source.value} 没有内置 llama.cpp 推荐模型。")
    extra: dict[str, Any] = {
        "backend": "llama",
        "managed_runtime": LLAMA_CPP_MANAGED_RUNTIME_MARKER,
    }
    if args.llama_binary_path:
        extra["llama_binary_path"] = str(args.llama_binary_path).strip()
    return SensoryProviderConfig(
        provider_id=str(args.provider_id or f"{source.value}_local"),
        source=source,
        mode=SensoryProviderMode.LOCAL,
        endpoint=str(args.endpoint or f"http://127.0.0.1:{DEFAULT_LLAMA_CPP_MANAGED_PORT}/v1"),
        model=model,
        extra=extra,
    ).normalized()


def _remote_managed_llama_model(plan: SensoryAudioSmokePlan) -> bool:
    return bool(plan.managed_runtime and plan.requires_model_download)


def _runtime_manifest_url(
    original_url: str,
    *,
    mirror_base_url: str,
    relative_archive_dir: str,
) -> str:
    filename = _url_filename(original_url)
    if relative_archive_dir:
        return f"{relative_archive_dir.strip().strip('/')}/{filename}"
    if mirror_base_url:
        return f"{mirror_base_url.strip().rstrip('/')}/{filename}"
    return original_url


def _url_filename(url: str) -> str:
    parsed = urlparse(str(url or ""))
    name = Path(parsed.path).name
    if not name:
        raise SensoryAudioRuntimeCliError(f"无法从 URL 提取 archive 文件名：{url}")
    return name


def _add_local_archive_metadata(data: dict[str, Any], archive_path: Path) -> None:
    if not archive_path.is_file():
        raise SensoryAudioRuntimeCliError(f"本地 archive 不存在：{archive_path}")
    data["sha256"] = _sha256_file(archive_path)
    data["size_bytes"] = archive_path.stat().st_size


def _runtime_manifest_check_path(args: argparse.Namespace) -> Path:
    manifest = args.manifest
    if manifest is not None:
        return Path(manifest).expanduser()
    return Path(args.base_dir) / "data" / "local_runtimes" / "llama_cpp" / "runtime_manifest.json"


def _required_manifest_platforms(args: argparse.Namespace) -> list[str]:
    platforms = [str(platform).strip().lower() for platform in args.require_platform if str(platform).strip()]
    if args.require_known_platforms:
        platforms.extend(_KNOWN_LLAMA_CPP_PLATFORM_KEYS)
    seen: set[str] = set()
    result: list[str] = []
    for platform in platforms:
        if platform in seen:
            continue
        seen.add(platform)
        result.append(platform)
    return result


def _check_manifest_package_archive(
    package: Any,
    manifest_dir: Path,
    archive_root: Path | None,
    issues: list[str],
) -> dict[str, Any]:
    archive_path = _manifest_archive_path(package.url, manifest_dir, archive_root)
    result: dict[str, Any] = {
        "package_id": package.package_id,
        "platform_key": package.platform_key,
        "url": package.url,
        "archive_path": str(archive_path) if archive_path is not None else "",
        "archive_exists": False,
        "size_ok": None,
        "sha256_ok": None,
    }
    if archive_path is None:
        return result
    if not archive_path.is_file():
        issues.append(f"{package.package_id} 缺少 archive：{archive_path}")
        return result
    result["archive_exists"] = True
    if package.size_bytes > 0:
        actual_size = archive_path.stat().st_size
        result["actual_size_bytes"] = actual_size
        result["size_ok"] = actual_size == package.size_bytes
        if not result["size_ok"]:
            issues.append(f"{package.package_id} archive 大小不匹配：{actual_size} != {package.size_bytes}")
    if package.sha256:
        actual_sha256 = _sha256_file(archive_path)
        result["actual_sha256"] = actual_sha256
        result["sha256_ok"] = actual_sha256 == package.sha256
        if not result["sha256_ok"]:
            issues.append(f"{package.package_id} archive sha256 不匹配")
    return result


def _manifest_archive_path(
    url: str,
    manifest_dir: Path,
    archive_root: Path | None,
) -> Path | None:
    filename = _url_filename(url)
    if archive_root is not None:
        return archive_root / filename
    parsed = urlparse(str(url or ""))
    if parsed.scheme == "file":
        return Path(url2pathname(parsed.path)).expanduser()
    if parsed.scheme:
        return None
    path = Path(str(url)).expanduser()
    if not path.is_absolute():
        path = manifest_dir / path
    return path


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _print_payload(payload: MappingPayload, *, pretty: bool) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2 if pretty else None))


MappingPayload = dict[str, Any]


if __name__ == "__main__":
    raise SystemExit(main())
