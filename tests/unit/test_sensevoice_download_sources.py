from __future__ import annotations

import io
import urllib.error
from pathlib import Path

import pytest

from plugins.optional.sakura_asr_sensevoice import _resources


VAD_URL = next(url for name, url, _size in _resources.FILES if name == "silero_vad.onnx")


@pytest.mark.parametrize("failure", ["http", "partial", "all", "size", "disk", "cancel"])
def test_vad_mirrors_preserve_model_publication_and_cancel(tmp_path, monkeypatch, failure):
    content = b"complete model fixture"
    monkeypatch.setattr(_resources, "FILES", (("silero_vad.onnx", VAD_URL, len(content)),))
    resources = _resources.ModelResources(tmp_path)
    resources.path.mkdir()
    old = resources.path / "old-model"
    old.write_bytes(b"previous installation")
    calls = []

    class BrokenStream(io.BytesIO):
        def read(self, size=-1):
            if self.tell():
                raise ConnectionResetError("mirror connection interrupted")
            return super().read(4)

    def open_url(request, *, timeout):
        calls.append(request.full_url)
        assert request.get_header("User-agent") == "Sakura-ASR/1"
        if failure == "all" or failure == "http" and len(calls) == 1:
            raise urllib.error.HTTPError(request.full_url, 503, "Unavailable", {}, None)
        if failure == "partial" and len(calls) == 1:
            return BrokenStream(content)
        if failure == "cancel":
            if len(calls) == len(_resources.GITHUB_MIRRORS) + 1:
                resources.cancelled.set()
            raise urllib.error.URLError("connection interrupted after cancellation")
        return io.BytesIO(content[:-1] if failure == "size" else content)

    monkeypatch.setattr(_resources, "urlopen_current_proxy", open_url)
    if failure == "disk":
        original_open = Path.open

        def open_file(path, mode="r", *args, **kwargs):
            if path.name == "silero_vad.onnx" and mode == "wb":
                raise OSError(28, "No space left on device")
            return original_open(path, mode, *args, **kwargs)

        monkeypatch.setattr(Path, "open", open_file)
    resources._install()
    candidates = [prefix + VAD_URL for prefix in _resources.GITHUB_MIRRORS] + [VAD_URL]
    if failure in {"http", "partial"}:
        assert calls == candidates[:2]
        assert resources.ready()
        assert (resources.path / "silero_vad.onnx").read_bytes() == content
        assert resources.downloaded == len(content)
    else:
        assert calls == (candidates if failure in {"all", "cancel"} else candidates[:1])
        assert resources.state == ("cancelled" if failure == "cancel" else "failed")
        assert old.read_bytes() == b"previous installation"
        assert not (resources.path / "silero_vad.onnx").exists()
        assert not list(tmp_path.glob(".install-*"))
        if failure == "size":
            assert resources.error == "ASR_DOWNLOAD_SIZE_MISMATCH"


@pytest.mark.parametrize("cancel", [False, True])
def test_modelscope_download_keeps_its_pinned_source(tmp_path, monkeypatch, cancel):
    source = "https://www.modelscope.cn/models/example/model/resolve/fixed/model.onnx"
    resources = _resources.ModelResources(tmp_path)
    monkeypatch.setattr(_resources, "FILES", (("model.onnx", source, 3),))
    calls = []

    def open_url(request, **_kwargs):
        calls.append(request.full_url)
        if cancel:
            resources.cancelled.set()
            raise urllib.error.URLError("connection interrupted after cancellation")
        return io.BytesIO(b"abc")

    monkeypatch.setattr(_resources, "urlopen_current_proxy", open_url)
    resources._install()
    assert calls == [source]
    assert resources.ready() is not cancel
    assert resources.state == ("cancelled" if cancel else "succeeded")
