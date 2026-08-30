#!/usr/bin/env python3
"""Verify a Tauri updater artifact without compiling a release-only Rust binary."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import os
import sys
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


class UpdaterSignatureError(ValueError):
    pass


def _outer_document(value: str, *, base64_code: str, utf8_code: str) -> str:
    try:
        decoded = base64.b64decode(value.strip(), validate=True)
    except (binascii.Error, ValueError) as error:
        raise UpdaterSignatureError(base64_code) from error
    try:
        return decoded.decode("utf-8")
    except UnicodeDecodeError as error:
        raise UpdaterSignatureError(utf8_code) from error


def _packet(value: str, *, length: int, code: str) -> bytes:
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as error:
        raise UpdaterSignatureError(code) from error
    if len(decoded) != length:
        raise UpdaterSignatureError(code)
    return decoded


def _artifact_message(path: Path, *, prehashed: bool) -> bytes:
    if not prehashed:
        return path.read_bytes()
    digest = hashlib.blake2b(digest_size=64)
    with path.open("rb") as artifact:
        while chunk := artifact.read(1024 * 1024):
            digest.update(chunk)
    return digest.digest()


def verify(public_key_encoded: str, artifact_path: Path, signature_encoded: str) -> None:
    public_document = _outer_document(
        public_key_encoded,
        base64_code="UPDATER_PUBLIC_KEY_BASE64_INVALID",
        utf8_code="UPDATER_PUBLIC_KEY_UTF8_INVALID",
    )
    public_lines = public_document.splitlines()
    if len(public_lines) < 2:
        raise UpdaterSignatureError("UPDATER_PUBLIC_KEY_INVALID")
    public_packet = _packet(
        public_lines[1], length=42, code="UPDATER_PUBLIC_KEY_INVALID"
    )
    if public_packet[:2] not in (b"Ed", b"ED"):
        raise UpdaterSignatureError("UPDATER_PUBLIC_KEY_INVALID")

    signature_document = _outer_document(
        signature_encoded,
        base64_code="UPDATER_SIGNATURE_BASE64_INVALID",
        utf8_code="UPDATER_SIGNATURE_UTF8_INVALID",
    )
    signature_lines = signature_document.splitlines()
    if len(signature_lines) < 4 or not signature_lines[2].startswith(
        "trusted comment: "
    ):
        raise UpdaterSignatureError("UPDATER_SIGNATURE_INVALID")
    signature_packet = _packet(
        signature_lines[1], length=74, code="UPDATER_SIGNATURE_INVALID"
    )
    global_signature = _packet(
        signature_lines[3], length=64, code="UPDATER_SIGNATURE_INVALID"
    )
    algorithm = signature_packet[:2]
    if algorithm not in (b"Ed", b"ED"):
        raise UpdaterSignatureError("UPDATER_SIGNATURE_INVALID")
    if public_packet[2:10] != signature_packet[2:10]:
        raise UpdaterSignatureError("UPDATER_SIGNATURE_VERIFICATION_FAILED")

    signature = signature_packet[10:]
    trusted_comment = signature_lines[2].removeprefix("trusted comment: ").encode()
    public_key = Ed25519PublicKey.from_public_bytes(public_packet[10:])
    try:
        public_key.verify(
            signature,
            _artifact_message(artifact_path, prehashed=algorithm == b"ED"),
        )
        public_key.verify(global_signature, signature + trusted_comment)
    except InvalidSignature as error:
        raise UpdaterSignatureError("UPDATER_SIGNATURE_VERIFICATION_FAILED") from error


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", type=Path)
    parser.add_argument("signature", type=Path)
    args = parser.parse_args()
    try:
        public_key = os.environ["SAKURA_UPDATER_PUBLIC_KEY"]
    except KeyError:
        print("UPDATER_PUBLIC_KEY_REQUIRED", file=sys.stderr)
        return 1
    try:
        signature = args.signature.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        print("UPDATER_SIGNATURE_READ_FAILED", file=sys.stderr)
        return 1
    try:
        verify(public_key, args.artifact, signature)
    except (OSError, UpdaterSignatureError) as error:
        code = (
            str(error)
            if isinstance(error, UpdaterSignatureError)
            else "UPDATER_ARTIFACT_READ_FAILED"
        )
        print(code, file=sys.stderr)
        return 1
    print(f"Verified updater signature: {args.artifact}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
