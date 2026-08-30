from __future__ import annotations

import base64
from pathlib import Path

import pytest

from app.core_host.screen_capture import (
    ScreenResourceRejected,
    consume_screen_resource,
    generation_resource_root,
    jpeg_dimensions,
)


GENERATION_ID = "00000000-0000-4000-8000-000000004006"
TOKEN = "a" * 32


def _jpeg(width: int, height: int) -> bytes:
    sof_payload = (
        b"\x08"
        + height.to_bytes(2, "big")
        + width.to_bytes(2, "big")
        + b"\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00"
    )
    scan = b"\xff\xda\x00\x0c\x03\x01\x00\x02\x11\x03\x11\x00\x3f\x00\x00"
    return b"\xff\xd8\xff\xc0" + (17).to_bytes(2, "big") + sof_payload + scan + b"\xff\xd9"


def _resource(tmp_path: Path, *, width: int = 3, height: int = 2) -> tuple[dict[str, object], Path, bytes]:
    image = _jpeg(width, height)
    root = generation_resource_root(GENERATION_ID, temp_root=tmp_path)
    root.mkdir(parents=True)
    path = root / f"{TOKEN}.jpg"
    path.write_bytes(image)
    return (
        {
            "generationId": GENERATION_ID,
            "resourceToken": TOKEN,
            "mimeType": "image/jpeg",
            "width": width,
            "height": height,
            "byteLength": len(image),
            "capturedAt": "2026-08-18T01:02:03Z",
            "screenName": "fixture monitor",
        },
        path,
        image,
    )


def test_screen_resource_is_validated_loaded_and_deleted_once(tmp_path: Path) -> None:
    descriptor, path, image = _resource(tmp_path)

    observation = consume_screen_resource(
        descriptor,
        generation_id=GENERATION_ID,
        temp_root=tmp_path,
    )

    assert (observation.width, observation.height) == (3, 2)
    assert observation.data_url == (
        "data:image/jpeg;base64," + base64.b64encode(image).decode("ascii")
    )
    assert not path.exists()
    with pytest.raises(ScreenResourceRejected, match="SCREEN_RESOURCE_FILE_INVALID"):
        consume_screen_resource(
            descriptor,
            generation_id=GENERATION_ID,
            temp_root=tmp_path,
        )


def test_screen_resource_rejects_descriptor_dimension_spoof_and_deletes_file(tmp_path: Path) -> None:
    descriptor, path, _ = _resource(tmp_path)
    descriptor["width"] = 4

    with pytest.raises(ScreenResourceRejected, match="SCREEN_RESOURCE_DIMENSION_MISMATCH"):
        consume_screen_resource(
            descriptor,
            generation_id=GENERATION_ID,
            temp_root=tmp_path,
        )

    assert not path.exists()


def test_screen_resource_rejects_non_token_paths_before_file_access(tmp_path: Path) -> None:
    descriptor, _, _ = _resource(tmp_path)
    descriptor["resourceToken"] = "../private"

    with pytest.raises(ScreenResourceRejected, match="SCREEN_RESOURCE_TOKEN_INVALID"):
        consume_screen_resource(
            descriptor,
            generation_id=GENERATION_ID,
            temp_root=tmp_path,
        )


def test_screen_resource_rejects_old_generation_without_touching_its_file(tmp_path: Path) -> None:
    descriptor, path, _ = _resource(tmp_path)

    with pytest.raises(ScreenResourceRejected, match="SCREEN_RESOURCE_GENERATION_MISMATCH"):
        consume_screen_resource(
            descriptor,
            generation_id="00000000-0000-4000-8000-000000004007",
            temp_root=tmp_path,
        )

    assert path.exists()


def test_screen_resource_rejects_symlink_without_touching_target(tmp_path: Path) -> None:
    descriptor, candidate, image = _resource(tmp_path)
    candidate.unlink()
    target = tmp_path / "outside.jpg"
    target.write_bytes(image)
    try:
        candidate.symlink_to(target)
    except OSError:
        pytest.skip("fixture symlinks are unavailable")

    with pytest.raises(ScreenResourceRejected, match="SCREEN_RESOURCE_FILE_INVALID"):
        consume_screen_resource(
            descriptor,
            generation_id=GENERATION_ID,
            temp_root=tmp_path,
        )

    assert target.read_bytes() == image
    assert not candidate.exists()


def test_jpeg_dimensions_requires_a_well_formed_sof_envelope() -> None:
    assert jpeg_dimensions(_jpeg(1280, 720)) == (1280, 720)
    with pytest.raises(ScreenResourceRejected, match="SCREEN_RESOURCE_IMAGE_INVALID"):
        jpeg_dimensions(b"not-an-image")
