from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.sensory import audio_deployment
from app.sensory.audio_runtime_doctor import build_sensory_audio_runtime_doctor_report
from app.sensory import huggingface as sensory_huggingface
from app.sensory.audio_models import llama_cpp_audio_cache_ready, recommended_llama_cpp_audio_model
from app.sensory.llama_cpp_runtime import LlamaCppRuntimePackageSpec
from app.sensory.models import SensorySource
from app.storage.paths import StoragePaths
from app.ui.settings import workers as settings_workers


def test_default_huggingface_query_is_source_specific() -> None:
    assert "vision" in settings_workers.default_huggingface_query_for_source(SensorySource.VISION)
    assert "speech" in settings_workers.default_huggingface_query_for_source(SensorySource.SPEECH)
    assert "audio" in settings_workers.default_huggingface_query_for_source(SensorySource.SOUND)
    assert settings_workers.primary_huggingface_task_filter_for_source(SensorySource.VISION) == "image-text-to-text"
    assert (
        settings_workers.primary_huggingface_task_filter_for_source(SensorySource.SPEECH)
        == "automatic-speech-recognition"
    )
    assert settings_workers.primary_huggingface_task_filter_for_source(SensorySource.SOUND) == "audio-classification"


def test_recommended_llama_cache_ready_requires_all_audio_files(tmp_path: Path) -> None:
    recommendation = recommended_llama_cpp_audio_model(SensorySource.SPEECH)
    assert recommendation is not None

    (tmp_path / "Qwen3-ASR-0.6B-Q8_0.gguf").write_text("gguf", encoding="utf-8")
    assert llama_cpp_audio_cache_ready(tmp_path, recommendation.include_patterns) is False

    (tmp_path / "mmproj-Qwen3-ASR-0.6B-Q8_0.gguf").write_text("gguf", encoding="utf-8")
    assert llama_cpp_audio_cache_ready(tmp_path, recommendation.include_patterns) is True


def test_search_huggingface_models_uses_hf_cli_and_parses_json(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[tuple[list[str], int]] = []

    monkeypatch.setattr(sensory_huggingface.shutil, "which", lambda name: "/usr/local/bin/hf" if name == "hf" else None)

    def fake_run(command, *, check, capture_output, text, timeout):  # type: ignore[no-untyped-def]
        calls.append((list(command), timeout))
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                [
                    {
                        "id": "Qwen/Qwen3-VL-4B-Instruct",
                        "pipeline_tag": "image-text-to-text",
                        "downloads": 123,
                        "likes": 45,
                    },
                    {
                        "id": "Qwen/Qwen3-VL-Embedding-8B",
                        "pipeline_tag": "sentence-similarity",
                        "tags": ["image-text-to-text", "qwen3_vl"],
                    },
                    {"modelId": "openai/whisper-large-v3", "downloads": 50},
                    {"id": "invalid-no-namespace"},
                ]
            ),
            stderr="",
        )

    monkeypatch.setattr(sensory_huggingface.subprocess, "run", fake_run)

    results = settings_workers.search_huggingface_models(
        SensorySource.VISION,
        "qwen vl",
        limit=2,
        timeout_seconds=9,
    )

    assert calls == [
        (
            [
                "/usr/local/bin/hf",
                "models",
                "list",
                "--search",
                "qwen vl",
                "--limit",
                "2",
                "--format",
                "json",
                "--filter",
                "image-text-to-text",
            ],
            9,
        )
    ]
    assert results == [
        {
            "repo_id": "Qwen/Qwen3-VL-4B-Instruct",
            "pipeline_tag": "image-text-to-text",
            "downloads": 123,
            "likes": 45,
            "compatibility": "clear",
            "compatibility_label": "明显兼容",
            "compatibility_reason": "主任务 image-text-to-text",
        },
    ]


def test_search_huggingface_models_falls_back_and_marks_uncertain_matches(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[list[str]] = []

    monkeypatch.setattr(sensory_huggingface.shutil, "which", lambda name: "/usr/local/bin/hf" if name == "hf" else None)

    def fake_run(command, *, check, capture_output, text, timeout):  # type: ignore[no-untyped-def]
        calls.append(list(command))
        if "--filter" in command:
            stdout = json.dumps(
                [
                    {
                        "id": "Qwen/Qwen3-VL-Embedding-8B",
                        "pipeline_tag": "sentence-similarity",
                        "tags": ["image-text-to-text", "qwen3_vl"],
                        "downloads": 10,
                    }
                ]
            )
        else:
            stdout = json.dumps(
                [
                    {
                        "id": "mlx-community/Qwen3-VL-4B-Instruct-4bit",
                        "tags": ["mlx", "qwen3_vl"],
                        "downloads": 20,
                    },
                    {
                        "id": "demo/text-only-model",
                        "pipeline_tag": "text-generation",
                        "downloads": 30,
                    },
                ]
            )
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(sensory_huggingface.subprocess, "run", fake_run)

    results = settings_workers.search_huggingface_models(
        SensorySource.VISION,
        "qwen vl mlx",
        limit=3,
        timeout_seconds=9,
    )

    assert len(calls) == 2
    assert "--filter" in calls[0]
    assert "--filter" not in calls[1]
    assert results[0]["repo_id"] == "mlx-community/Qwen3-VL-4B-Instruct-4bit"
    assert results[0]["compatibility"] == "possible"
    assert results[0]["compatibility_label"] == "可能兼容"
    assert results[1]["repo_id"] == "demo/text-only-model"
    assert results[1]["compatibility"] == "unknown"
    assert results[1]["compatibility_label"] == "类型未验证"


def test_download_huggingface_model_uses_local_dir(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    calls: list[list[str]] = []
    target = tmp_path / "hf" / "qwen"

    monkeypatch.setattr(sensory_huggingface.shutil, "which", lambda name: "/usr/bin/hf" if name == "hf" else None)

    def fake_run(command, *, check, capture_output, text, timeout):  # type: ignore[no-untyped-def]
        calls.append(list(command))
        return subprocess.CompletedProcess(command, 0, stdout="downloaded", stderr="")

    monkeypatch.setattr(sensory_huggingface.subprocess, "run", fake_run)

    result = sensory_huggingface.download_huggingface_model(
        "Qwen/Qwen3-VL-4B-Instruct",
        target,
        timeout_seconds=33,
    )

    assert target.is_dir()
    assert calls == [
        [
            "/usr/bin/hf",
            "download",
            "Qwen/Qwen3-VL-4B-Instruct",
            "--local-dir",
            str(target),
        ]
    ]
    assert result["repo_id"] == "Qwen/Qwen3-VL-4B-Instruct"
    assert result["local_dir"] == str(target)
    assert result["message"] == "downloaded"


def test_download_huggingface_model_passes_include_patterns(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    calls: list[list[str]] = []
    target = tmp_path / "hf" / "qwen-asr"

    monkeypatch.setattr(sensory_huggingface.shutil, "which", lambda name: "/usr/bin/hf" if name == "hf" else None)

    def fake_run(command, *, check, capture_output, text, timeout):  # type: ignore[no-untyped-def]
        calls.append(list(command))
        return subprocess.CompletedProcess(command, 0, stdout="downloaded", stderr="")

    monkeypatch.setattr(sensory_huggingface.subprocess, "run", fake_run)

    result = sensory_huggingface.download_huggingface_model(
        "ggml-org/Qwen3-ASR-0.6B-GGUF",
        target,
        include_patterns=("*Q8_0.gguf", "mmproj-*.gguf"),
        timeout_seconds=33,
    )

    assert calls == [
        [
            "/usr/bin/hf",
            "download",
            "ggml-org/Qwen3-ASR-0.6B-GGUF",
            "--local-dir",
            str(target),
            "--include",
            "*Q8_0.gguf",
            "--include",
            "mmproj-*.gguf",
        ]
    ]
    assert result["include_patterns"] == ["*Q8_0.gguf", "mmproj-*.gguf"]


def test_download_huggingface_model_falls_back_to_builtin_http_for_included_files(
    monkeypatch,
    tmp_path: Path,
) -> None:  # type: ignore[no-untyped-def]
    target = tmp_path / "hf" / "qwen-asr"
    urls: list[str] = []

    monkeypatch.setattr(sensory_huggingface.shutil, "which", lambda _name: None)

    def fake_urlopen(request, timeout):  # type: ignore[no-untyped-def]
        urls.append(str(getattr(request, "full_url", "")))
        if "/api/models/" in urls[-1]:
            return _FakeResponse(
                json.dumps(
                    {
                        "siblings": [
                            {"rfilename": "README.md"},
                            {"rfilename": "Qwen3-ASR-0.6B-Q8_0.gguf"},
                            {"rfilename": "mmproj-Qwen3-ASR-0.6B-Q8_0.gguf"},
                            {"rfilename": "Qwen3-ASR-0.6B-bf16.gguf"},
                        ]
                    }
                ).encode("utf-8")
            )
        return _FakeResponse(b"gguf")

    monkeypatch.setattr(sensory_huggingface, "urlopen", fake_urlopen)

    result = sensory_huggingface.download_huggingface_model(
        "ggml-org/Qwen3-ASR-0.6B-GGUF",
        target,
        include_patterns=("Qwen3-ASR-0.6B-Q8_0.gguf", "mmproj-Qwen3-ASR-0.6B-Q8_0.gguf"),
        timeout_seconds=33,
    )

    assert result["download_method"] == "builtin_http"
    assert result["downloaded_files"] == [
        "Qwen3-ASR-0.6B-Q8_0.gguf",
        "mmproj-Qwen3-ASR-0.6B-Q8_0.gguf",
    ]
    assert (target / "Qwen3-ASR-0.6B-Q8_0.gguf").read_bytes() == b"gguf"
    assert (target / "mmproj-Qwen3-ASR-0.6B-Q8_0.gguf").read_bytes() == b"gguf"
    assert len(urls) == 3


def test_download_huggingface_model_without_hf_refuses_unbounded_builtin_download(
    monkeypatch,
    tmp_path: Path,
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(sensory_huggingface.shutil, "which", lambda _name: None)

    with pytest.raises(RuntimeError, match="整个仓库"):
        sensory_huggingface.download_huggingface_model(
            "Qwen/Qwen3-VL-4B-Instruct",
            tmp_path / "model",
        )


def test_prepare_llama_cpp_audio_backend_downloads_recommended_model(
    monkeypatch,
    tmp_path: Path,
) -> None:  # type: ignore[no-untyped-def]
    binary = tmp_path / "llama-server"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    calls: list[tuple[str, Path, tuple[str, ...]]] = []

    monkeypatch.setattr(audio_deployment, "discover_llama_server_binary", lambda base_dir: str(binary))

    def fake_download(repo_id, local_dir, *, include_patterns, timeout_seconds):  # type: ignore[no-untyped-def]
        calls.append((repo_id, Path(local_dir), tuple(include_patterns)))
        Path(local_dir).mkdir(parents=True, exist_ok=True)
        (Path(local_dir) / "Qwen3-ASR-0.6B-Q8_0.gguf").write_text("gguf", encoding="utf-8")
        (Path(local_dir) / "mmproj-Qwen3-ASR-0.6B-Q8_0.gguf").write_text("gguf", encoding="utf-8")
        return {"repo_id": repo_id, "local_dir": str(local_dir), "message": "downloaded"}

    monkeypatch.setattr(audio_deployment, "download_huggingface_model", fake_download)

    payload = audio_deployment.prepare_llama_cpp_audio_backend(
        tmp_path,
        SensorySource.SPEECH,
        download_model=True,
        timeout_seconds=33,
    )

    model = payload["model"]
    assert calls == [
        (
            "ggml-org/Qwen3-ASR-0.6B-GGUF",
            Path(model["local_dir"]),
            ("Qwen3-ASR-0.6B-Q8_0.gguf", "mmproj-Qwen3-ASR-0.6B-Q8_0.gguf"),
        )
    ]
    assert payload["runtime"]["binary_path"] == str(binary)
    assert model["downloaded"] is True
    assert model["gguf_count"] == 2
    assert model["disk_space"]["ok"] is True


def test_prepare_llama_cpp_audio_backend_refuses_runtime_download_without_consent(
    monkeypatch,
    tmp_path: Path,
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(audio_deployment, "discover_llama_server_binary", lambda base_dir: "")

    def fail_catalog(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("runtime catalog should not be fetched without consent")

    monkeypatch.setattr(audio_deployment, "fetch_llama_cpp_runtime_package_catalog", fail_catalog)

    with pytest.raises(RuntimeError, match="llama-server"):
        audio_deployment.prepare_llama_cpp_audio_backend(
            tmp_path,
            SensorySource.SPEECH,
            download_runtime=False,
            download_model=False,
        )


def test_prepare_llama_cpp_audio_backend_uses_local_audio_model_manifest(
    monkeypatch,
    tmp_path: Path,
) -> None:  # type: ignore[no-untyped-def]
    binary = tmp_path / "llama-server"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    manifest_dir = StoragePaths(tmp_path).sensory_models_cache_dir
    archive_dir = manifest_dir / "archives"
    archive_dir.mkdir(parents=True)
    model_file = archive_dir / "Qwen3-ASR-0.6B-Q8_0.gguf"
    mmproj_file = archive_dir / "mmproj-Qwen3-ASR-0.6B-Q8_0.gguf"
    model_file.write_bytes(b"model")
    mmproj_file.write_bytes(b"mmproj")
    (manifest_dir / "audio_model_manifest.json").write_text(
        json.dumps(
            {
                "models": [
                    {
                        "source": "speech",
                        "repo_id": "ggml-org/Qwen3-ASR-0.6B-GGUF",
                        "files": [
                            {
                                "filename": model_file.name,
                                "url": f"archives/{model_file.name}",
                                "size_bytes": model_file.stat().st_size,
                                "sha256": hashlib.sha256(model_file.read_bytes()).hexdigest(),
                            },
                            {
                                "filename": mmproj_file.name,
                                "url": f"archives/{mmproj_file.name}",
                                "size_bytes": mmproj_file.stat().st_size,
                                "sha256": hashlib.sha256(mmproj_file.read_bytes()).hexdigest(),
                            },
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(audio_deployment, "discover_llama_server_binary", lambda base_dir: str(binary))

    def fail_hf_download(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("local audio model manifest should avoid Hugging Face download")

    monkeypatch.setattr(audio_deployment, "download_huggingface_model", fail_hf_download)

    payload = audio_deployment.prepare_llama_cpp_audio_backend(
        tmp_path,
        SensorySource.SPEECH,
        download_model=True,
    )

    model = payload["model"]
    local_dir = Path(model["local_dir"])
    assert model["gguf_count"] == 2
    assert model["download_message"] == "copied 2 file(s) from local audio model manifest"
    assert (local_dir / model_file.name).read_bytes() == b"model"
    assert (local_dir / mmproj_file.name).read_bytes() == b"mmproj"


def test_llama_cpp_runtime_download_preflight_selects_package_and_checks_space(
    monkeypatch,
    tmp_path: Path,
) -> None:  # type: ignore[no-untyped-def]
    package = LlamaCppRuntimePackageSpec(
        package_id="b1-macos",
        label="llama.cpp b1 macOS",
        platform_key="macos-arm64",
        url="https://example.invalid/llama-b1-bin-macos-arm64.tar.gz",
        archive_format="tar.gz",
        binary_relpath="llama-server",
        size_bytes=1024,
    )
    checks: list[tuple[Path, int]] = []
    monkeypatch.setattr(audio_deployment, "discover_llama_server_binary", lambda base_dir: "")
    monkeypatch.setattr(
        audio_deployment,
        "fetch_llama_cpp_runtime_package_catalog",
        lambda **_kwargs: SimpleNamespace(source="manifest:test", packages=(package,)),
    )
    monkeypatch.setattr(
        audio_deployment,
        "build_disk_space_check",
        lambda path, required_bytes: checks.append((Path(path), required_bytes))
        or {
            "ok": True,
            "needed_bytes": required_bytes + 512,
            "available_bytes": 4096,
        },
    )

    preflight = audio_deployment.build_llama_cpp_runtime_download_preflight(tmp_path)

    assert preflight["required"] is True
    assert preflight["ok"] is True
    assert preflight["package_source"] == "manifest:test"
    assert preflight["package"]["package_id"] == "b1-macos"
    assert preflight["estimated_download_bytes"] == 1024
    assert preflight["estimated_required_bytes"] == 2048
    assert preflight["download_hint"] == "1.0 KB"
    assert checks == [
        (
            StoragePaths(tmp_path).llama_cpp_runtime_dir
            / "_downloads"
            / "llama-b1-bin-macos-arm64.tar.gz",
            2048,
        )
    ]


def test_audio_runtime_doctor_reports_local_audio_model_manifest(tmp_path: Path) -> None:
    manifest_dir = StoragePaths(tmp_path).sensory_models_cache_dir
    archive_dir = manifest_dir / "archives"
    archive_dir.mkdir(parents=True)
    model_file = archive_dir / "Qwen3-ASR-0.6B-Q8_0.gguf"
    mmproj_file = archive_dir / "mmproj-Qwen3-ASR-0.6B-Q8_0.gguf"
    model_file.write_bytes(b"model")
    mmproj_file.write_bytes(b"mmproj")
    (manifest_dir / "audio_model_manifest.json").write_text(
        json.dumps(
            {
                "models": [
                    {
                        "source": "speech",
                        "repo_id": "ggml-org/Qwen3-ASR-0.6B-GGUF",
                        "files": [
                            {"filename": model_file.name, "url": f"archives/{model_file.name}"},
                            {"filename": mmproj_file.name, "url": f"archives/{mmproj_file.name}"},
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    report = build_sensory_audio_runtime_doctor_report(tmp_path)

    speech_cache = report["model_cache"]["speech"]
    assert speech_cache["model_manifest"]["manifest_path"] == str(manifest_dir / "audio_model_manifest.json")
    assert speech_cache["model_manifest_error"] == ""
    assert any("本地音频模型 manifest" in action for action in report["next_actions"])


def test_prepare_llama_cpp_audio_backend_refuses_model_download_when_disk_is_low(
    monkeypatch,
    tmp_path: Path,
) -> None:  # type: ignore[no-untyped-def]
    binary = tmp_path / "llama-server"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.setattr(audio_deployment, "discover_llama_server_binary", lambda base_dir: str(binary))
    monkeypatch.setattr(
        audio_deployment,
        "build_disk_space_check",
        lambda path, required_bytes: {
            "ok": False,
            "available_bytes": 1024,
            "needed_bytes": 2048,
            "required_bytes": required_bytes,
        },
    )

    def fail_download(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("model download should not start when disk is low")

    monkeypatch.setattr(audio_deployment, "download_huggingface_model", fail_download)

    with pytest.raises(RuntimeError, match="磁盘空间不足"):
        audio_deployment.prepare_llama_cpp_audio_backend(
            tmp_path,
            SensorySource.SPEECH,
            download_model=True,
        )


def test_prepare_llama_cpp_audio_backend_reuses_cached_recommended_model(
    monkeypatch,
    tmp_path: Path,
) -> None:  # type: ignore[no-untyped-def]
    binary = tmp_path / "llama-server"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    cache_dir = StoragePaths(tmp_path).sensory_model_cache_for(
        "sound",
        "ggml-org/ultravox-v0_5-llama-3_2-1b-GGUF",
    )
    cache_dir.mkdir(parents=True)
    (cache_dir / "Llama-3.2-1B-Instruct-Q4_K_M.gguf").write_text("gguf", encoding="utf-8")
    (cache_dir / "mmproj-ultravox-v0_5-llama-3_2-1b-f16.gguf").write_text("gguf", encoding="utf-8")

    monkeypatch.setattr(audio_deployment, "discover_llama_server_binary", lambda base_dir: str(binary))

    def fail_download(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("cached model should not be downloaded")

    monkeypatch.setattr(audio_deployment, "download_huggingface_model", fail_download)

    payload = audio_deployment.prepare_llama_cpp_audio_backend(
        tmp_path,
        SensorySource.SOUND,
        download_model=True,
    )

    model = payload["model"]
    assert model["cached_before"] is True
    assert model["downloaded"] is False
    assert model["local_dir"] == str(cache_dir)
    assert model["include_patterns"] == ["Llama-3.2-1B-Instruct-Q4_K_M.gguf", "mmproj-*.gguf"]


def test_prepare_llama_cpp_audio_backend_rejects_empty_model_download(
    monkeypatch,
    tmp_path: Path,
) -> None:  # type: ignore[no-untyped-def]
    binary = tmp_path / "llama-server"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.setattr(audio_deployment, "discover_llama_server_binary", lambda base_dir: str(binary))

    def fake_download(repo_id, local_dir, *, include_patterns, timeout_seconds):  # type: ignore[no-untyped-def]
        Path(local_dir).mkdir(parents=True, exist_ok=True)
        return {"repo_id": repo_id, "local_dir": str(local_dir), "message": "downloaded nothing"}

    monkeypatch.setattr(audio_deployment, "download_huggingface_model", fake_download)

    with pytest.raises(RuntimeError, match="未找到 GGUF"):
        audio_deployment.prepare_llama_cpp_audio_backend(
            tmp_path,
            SensorySource.SPEECH,
            download_model=True,
        )


def test_huggingface_cli_missing_fails_with_install_hint(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(sensory_huggingface.shutil, "which", lambda _name: None)

    with pytest.raises(RuntimeError, match="Hugging Face CLI"):
        sensory_huggingface.download_huggingface_model("Qwen/Qwen3-VL-4B-Instruct", tmp_path / "model")


class _FakeResponse:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.offset = 0

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            size = len(self.data) - self.offset
        chunk = self.data[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk
