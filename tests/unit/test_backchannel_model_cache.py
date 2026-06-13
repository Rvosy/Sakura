from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from app.backchannel.model_cache import (
    BACKCHANNEL_MODEL_CACHE_NAME,
    BackchannelModelImportError,
    backchannel_model_cache_kwargs,
    backchannel_model_cached,
    download_backchannel_model,
    import_backchannel_model_archive,
)


def _write_model_zip(path: Path, *, prefix: str = "") -> None:
    root = prefix.strip("/")
    base = f"{root}/" if root else ""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(f"{base}snapshots/revision/model.safetensors", b"fake")
        zf.writestr(f"{base}refs/main", "revision")


@pytest.mark.parametrize(
    "prefix",
    [
        "",
        BACKCHANNEL_MODEL_CACHE_NAME,
        f"hub/{BACKCHANNEL_MODEL_CACHE_NAME}",
        f"hf-cache/hub/{BACKCHANNEL_MODEL_CACHE_NAME}",
    ],
)
def test_import_backchannel_model_archive_structures(tmp_path: Path, prefix: str) -> None:
    archive = tmp_path / "model.zip"
    _write_model_zip(archive, prefix=prefix)

    result = import_backchannel_model_archive(archive, tmp_path)

    assert result.model_name == "BAAI/bge-small-zh-v1.5"
    assert result.snapshot_count == 1
    assert result.model_dir == tmp_path / "runtime" / "hf-cache" / "hub" / BACKCHANNEL_MODEL_CACHE_NAME
    assert backchannel_model_cached(tmp_path) is True
    assert backchannel_model_cache_kwargs(tmp_path) == {
        "cache_folder": str(tmp_path / "runtime" / "hf-cache" / "hub"),
        "local_files_only": True,
    }


def test_import_backchannel_model_archive_rejects_wrong_model(tmp_path: Path) -> None:
    archive = tmp_path / "wrong.zip"
    _write_model_zip(archive, prefix="models--other--model")

    with pytest.raises(BackchannelModelImportError):
        import_backchannel_model_archive(archive, tmp_path)

    assert backchannel_model_cached(tmp_path) is False


def test_download_backchannel_model_uses_project_hf_cache(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    from app.backchannel import model_cache as model_cache_module

    calls: list[tuple[str, Path]] = []

    def fake_download(repo_id: str, cache_folder: Path) -> str:
        calls.append((repo_id, cache_folder))
        snapshot = cache_folder / BACKCHANNEL_MODEL_CACHE_NAME / "snapshots" / "revision"
        snapshot.mkdir(parents=True)
        (snapshot / "model.safetensors").write_bytes(b"fake")
        return str(snapshot)

    monkeypatch.setattr(model_cache_module, "_download_hf_snapshot", fake_download)

    result = download_backchannel_model(tmp_path)

    expected_cache = tmp_path / "runtime" / "hf-cache" / "hub"
    assert calls == [("BAAI/bge-small-zh-v1.5", expected_cache)]
    assert result.cache_folder == expected_cache
    assert result.model_dir == expected_cache / BACKCHANNEL_MODEL_CACHE_NAME
    assert result.snapshot_count == 1
    assert backchannel_model_cached(tmp_path) is True
