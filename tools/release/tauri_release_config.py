#!/usr/bin/env python3
"""Generate the platform-specific Tauri bundle overlay for a staged release."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


DEFAULT_UPDATER_ENDPOINT = (
    "https://github.com/Rvosy/Sakura/releases/latest/download/latest.json"
)


def build_config(
    *,
    target: str,
    updater: bool,
    endpoint: str,
    public_key: str,
    updater_artifacts: bool | None = None,
    windows_certificate_thumbprint: str = "",
) -> dict[str, object]:
    create_updater_artifacts = updater if updater_artifacts is None else updater_artifacts
    if create_updater_artifacts and not updater:
        raise ValueError("UPDATER_ARTIFACTS_REQUIRE_UPDATER")
    resources = {
        "release-staging/VERSION": "VERSION",
        "release-staging/runtime-manifest.json": "runtime-manifest.json",
        "release-staging/python": "python",
        "release-staging/core": "core",
        "release-staging/plugins": "plugins",
    }
    config: dict[str, object] = {
        "bundle": {
            "active": True,
            "resources": resources,
            "createUpdaterArtifacts": create_updater_artifacts,
            "targets": ["nsis"] if target == "windows-x64" else ["app", "dmg"],
        }
    }
    if target not in {"windows-x64", "macos-arm64"}:
        raise ValueError("RELEASE_TARGET_UNSUPPORTED")
    if windows_certificate_thumbprint:
        if target != "windows-x64":
            raise ValueError("WINDOWS_CERTIFICATE_TARGET_INVALID")
        config["bundle"]["windows"] = {
            "certificateThumbprint": windows_certificate_thumbprint.strip(),
            "digestAlgorithm": "sha256",
            "timestampUrl": "http://timestamp.digicert.com",
        }
    if updater:
        if not endpoint.startswith("https://") or not public_key.strip():
            raise ValueError("UPDATER_RELEASE_CONFIGURATION_MISSING")
        config["plugins"] = {
            "updater": {
                "endpoints": [endpoint],
                "pubkey": public_key.strip(),
                "windows": {"installMode": "passive"},
            }
        }
    return config


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True, choices=("windows-x64", "macos-arm64"))
    parser.add_argument("--output", required=True, type=Path)
    updater_mode = parser.add_mutually_exclusive_group()
    updater_mode.add_argument("--updater", action="store_true")
    updater_mode.add_argument("--updater-client", action="store_true")
    parser.add_argument(
        "--endpoint",
        default=os.environ.get(
            "SAKURA_UPDATER_ENDPOINT",
            DEFAULT_UPDATER_ENDPOINT,
        ),
    )
    parser.add_argument("--public-key", default=os.environ.get("SAKURA_UPDATER_PUBLIC_KEY", ""))
    parser.add_argument(
        "--windows-certificate-thumbprint",
        default=os.environ.get("WINDOWS_CERTIFICATE_THUMBPRINT", ""),
    )
    args = parser.parse_args()
    updater_enabled = args.updater or args.updater_client
    config = build_config(
        target=args.target,
        updater=updater_enabled,
        endpoint=args.endpoint,
        public_key=args.public_key,
        updater_artifacts=args.updater,
        windows_certificate_thumbprint=args.windows_certificate_thumbprint,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
