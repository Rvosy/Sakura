"""Qt-free validation and one-shot loading for Runtime v2 screen resources."""

from __future__ import annotations

import base64
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.agent.screen_observation import ScreenObservation


SCREEN_CAPTURE_CAPABILITY = "assistant.screen-capture-v2"
SCREEN_RESOURCE_DIRECTORY = "sakura-runtime-v2-screen-resources"
SCREEN_RESOURCE_MAX_BYTES = 24 * 1024 * 1024
SCREEN_RESOURCE_MAX_PIXELS = 32_000_000
_TOKEN_PATTERN = re.compile(r"[0-9a-f]{32}")
_GENERATION_PATTERN = re.compile(r"[0-9a-f-]{8,64}")
_JPEG_SOF_MARKERS = frozenset(
    {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
)
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
    """Read and delete one generation-private JPEG after repeating every trust check."""

    if not isinstance(descriptor, Mapping) or set(descriptor) != _RESOURCE_FIELDS:
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
    if byte_length > SCREEN_RESOURCE_MAX_BYTES or width * height > SCREEN_RESOURCE_MAX_PIXELS:
        raise ScreenResourceRejected("SCREEN_RESOURCE_LIMIT_EXCEEDED")
    captured_at = descriptor.get("capturedAt")
    screen_name = descriptor.get("screenName")
    if (
        not isinstance(captured_at, str)
        or not captured_at
        or len(captured_at) > 64
        or not isinstance(screen_name, str)
        or not screen_name
        or len(screen_name) > 128
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
        if stat.st_size != byte_length or stat.st_size > SCREEN_RESOURCE_MAX_BYTES:
            raise ScreenResourceRejected("SCREEN_RESOURCE_LENGTH_MISMATCH")
        image_bytes = resolved.read_bytes()
        actual_width, actual_height = jpeg_dimensions(image_bytes)
        if (actual_width, actual_height) != (width, height):
            raise ScreenResourceRejected("SCREEN_RESOURCE_DIMENSION_MISMATCH")
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


def jpeg_dimensions(data: bytes) -> tuple[int, int]:
    """Return JPEG SOF dimensions without importing Qt or another image runtime."""

    if len(data) < 4 or data[:2] != b"\xff\xd8" or data[-2:] != b"\xff\xd9":
        raise ScreenResourceRejected("SCREEN_RESOURCE_IMAGE_INVALID")
    cursor = 2
    dimensions: tuple[int, int] | None = None
    while cursor + 1 < len(data):
        if data[cursor] != 0xFF:
            cursor += 1
            continue
        while cursor < len(data) and data[cursor] == 0xFF:
            cursor += 1
        if cursor >= len(data):
            break
        marker = data[cursor]
        cursor += 1
        if marker in {0x01, 0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
            continue
        if cursor + 2 > len(data):
            break
        segment_length = int.from_bytes(data[cursor : cursor + 2], "big")
        if segment_length < 2 or cursor + segment_length > len(data):
            break
        if marker in _JPEG_SOF_MARKERS:
            if segment_length < 7:
                break
            height = int.from_bytes(data[cursor + 3 : cursor + 5], "big")
            width = int.from_bytes(data[cursor + 5 : cursor + 7], "big")
            if width <= 0 or height <= 0 or width * height > SCREEN_RESOURCE_MAX_PIXELS:
                break
            dimensions = (width, height)
        elif marker == 0xDA:
            if dimensions is not None and segment_length >= 8:
                return dimensions
            break
        cursor += segment_length
    raise ScreenResourceRejected("SCREEN_RESOURCE_IMAGE_INVALID")


def _positive_integer(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ScreenResourceRejected(code)
    return value
