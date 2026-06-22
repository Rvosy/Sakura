from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from app.sensory.models import SensorySource
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


def test_search_huggingface_models_uses_hf_cli_and_parses_json(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[tuple[list[str], int]] = []

    monkeypatch.setattr(settings_workers.shutil, "which", lambda name: "/usr/local/bin/hf" if name == "hf" else None)

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

    monkeypatch.setattr(settings_workers.subprocess, "run", fake_run)

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

    monkeypatch.setattr(settings_workers.shutil, "which", lambda name: "/usr/local/bin/hf" if name == "hf" else None)

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

    monkeypatch.setattr(settings_workers.subprocess, "run", fake_run)

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

    monkeypatch.setattr(settings_workers.shutil, "which", lambda name: "/usr/bin/hf" if name == "hf" else None)

    def fake_run(command, *, check, capture_output, text, timeout):  # type: ignore[no-untyped-def]
        calls.append(list(command))
        return subprocess.CompletedProcess(command, 0, stdout="downloaded", stderr="")

    monkeypatch.setattr(settings_workers.subprocess, "run", fake_run)

    result = settings_workers.download_huggingface_model(
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


def test_huggingface_cli_missing_fails_with_install_hint(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(settings_workers.shutil, "which", lambda _name: None)

    with pytest.raises(RuntimeError, match="Hugging Face CLI"):
        settings_workers.download_huggingface_model("Qwen/Qwen3-VL-4B-Instruct", tmp_path / "model")
