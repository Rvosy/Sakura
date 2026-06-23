from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, Sequence

from app.config.settings_service import AppSettingsService
from app.sensory.audio_models import recommended_llama_cpp_audio_model
from app.sensory.audio_smoke import (
    SensoryAudioSmokePlan,
    build_sensory_audio_smoke_plan,
    run_sensory_audio_smoke_test,
)
from app.sensory.llama_cpp_runtime import (
    DEFAULT_LLAMA_CPP_MANAGED_PORT,
    LLAMA_CPP_MANAGED_RUNTIME_MARKER,
    discover_llama_server_binary,
    fetch_llama_cpp_runtime_package_catalog,
    install_llama_cpp_runtime_package,
    llama_cpp_platform_key,
    select_llama_cpp_runtime_package,
)
from app.sensory.models import SensoryProviderMode, SensorySource, coerce_sensory_source
from app.sensory.settings import SensoryProviderConfig


class SensoryAudioRuntimeCliError(RuntimeError):
    """Raised for expected CLI validation failures."""


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "install-runtime":
            return _run_install_runtime(args)
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


def _print_payload(payload: MappingPayload, *, pretty: bool) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2 if pretty else None))


MappingPayload = dict[str, Any]


if __name__ == "__main__":
    raise SystemExit(main())
