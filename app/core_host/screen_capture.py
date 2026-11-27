"""Qt-free validation and one-shot loading for Runtime v2 screen resources."""

from __future__ import annotations

import base64
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.plugin_sdk.sakura_assistant_contract import ScreenObservation


SCREEN_CAPTURE_CAPABILITY = "assistant.screen-capture-v2"
SCREEN_RESOURCE_DIRECTORY = "sakura-runtime-v2-screen-resources"
SCREEN_RESOURCE_MAX_BYTES = 24 * 1024 * 1024
_TOKEN_PATTERN = re.compile(r"[0-9a-f]{32}")
_GENERATION_PATTERN = re.compile(r"[0-9a-f-]{8,64}")
_RESOURCE_FIELDS = frozenset(
    {
        "generationId",
        "resourceToken",
        "mimeType",
        "width",
        "height",
        "byteLength",
        "capturedAt",
        "screenName",
    }
)


class ScreenResourceRejected(ValueError):
    """A stable rejection that never includes a private path or image content."""


def generation_resource_root(generation_id: str, *, temp_root: Path | None = None) -> Path:
    if not isinstance(generation_id, str) or _GENERATION_PATTERN.fullmatch(generation_id) is None:
        raise ScreenResourceRejected("SCREEN_RESOURCE_GENERATION_INVALID")
    base = Path(temp_root) if temp_root is not None else Path(tempfile.gettempdir())
    return base / SCREEN_RESOURCE_DIRECTORY / generation_id


def consume_screen_resource(
    descriptor: Mapping[str, Any],
    *,
    generation_id: str,
    temp_root: Path | None = None,
) -> ScreenObservation:
    """Consume a native capture through its generation-private token."""

    if not isinstance(descriptor, Mapping) or not _RESOURCE_FIELDS.issubset(descriptor):
        raise ScreenResourceRejected("SCREEN_RESOURCE_DESCRIPTOR_INVALID")
    if descriptor.get("generationId") != generation_id:
        raise ScreenResourceRejected("SCREEN_RESOURCE_GENERATION_MISMATCH")
    token = descriptor.get("resourceToken")
    if not isinstance(token, str) or _TOKEN_PATTERN.fullmatch(token) is None:
        raise ScreenResourceRejected("SCREEN_RESOURCE_TOKEN_INVALID")
    if descriptor.get("mimeType") != "image/jpeg":
        raise ScreenResourceRejected("SCREEN_RESOURCE_MIME_INVALID")
    width = _positive_integer(descriptor.get("width"), "SCREEN_RESOURCE_WIDTH_INVALID")
    height = _positive_integer(descriptor.get("height"), "SCREEN_RESOURCE_HEIGHT_INVALID")
    byte_length = _positive_integer(
        descriptor.get("byteLength"), "SCREEN_RESOURCE_LENGTH_INVALID"
    )
    captured_at = descriptor.get("capturedAt")
    screen_name = descriptor.get("screenName")
    if (
        not isinstance(captured_at, str)
        or not captured_at
        or not isinstance(screen_name, str)
        or not screen_name
    ):
        raise ScreenResourceRejected("SCREEN_RESOURCE_METADATA_INVALID")

    root = generation_resource_root(generation_id, temp_root=temp_root)
    candidate = root / f"{token}.jpg"
    try:
        resolved_root = root.resolve(strict=True)
        if candidate.is_symlink() or not candidate.is_file():
            raise ScreenResourceRejected("SCREEN_RESOURCE_FILE_INVALID")
        resolved = candidate.resolve(strict=True)
        if resolved.parent != resolved_root:
            raise ScreenResourceRejected("SCREEN_RESOURCE_PATH_ESCAPE")
        stat = resolved.stat()
        if stat.st_size <= 0 or stat.st_size > SCREEN_RESOURCE_MAX_BYTES:
            raise ScreenResourceRejected("SCREEN_RESOURCE_LENGTH_MISMATCH")
        image_bytes = resolved.read_bytes()
        return ScreenObservation(
            data_url=f"data:image/jpeg;base64,{base64.b64encode(image_bytes).decode('ascii')}",
            width=width,
            height=height,
            captured_at=captured_at,
            screen_name=screen_name,
        )
    except ScreenResourceRejected:
        raise
    except OSError as error:
        raise ScreenResourceRejected("SCREEN_RESOURCE_READ_FAILED") from error
    finally:
        # `candidate` is an exact token-derived child. Unlinking it removes a rejected symlink
        # itself rather than anything it may target.
        try:
            candidate.unlink(missing_ok=True)
        except OSError:
            pass


def _positive_integer(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ScreenResourceRejected(code)
    return value
