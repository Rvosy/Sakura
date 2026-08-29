from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest


def test_fastembed_adapter_forwards_local_model_kwargs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_root = Path(__file__).parents[2] / "plugins" / "builtin" / "sakura_mem0"
    imported_before = set(sys.modules)
    monkeypatch.syspath_prepend(str(plugin_root))
    try:
        module = importlib.import_module("mem0.embeddings.fastembed")
        config_module = importlib.import_module("mem0.configs.embeddings.base")
        captured: dict[str, object] = {}

        class FakeTextEmbedding:
            embedding_size = 384

            def __init__(self, **kwargs: object) -> None:
                captured.update(kwargs)

        monkeypatch.setattr(module, "TextEmbedding", FakeTextEmbedding)
        config = config_module.BaseEmbedderConfig(
            model="sentence-transformers/all-MiniLM-L6-v2",
            embedding_dims=384,
            model_kwargs={
                "specific_model_path": "D:/fixed/local/snapshot",
                "local_files_only": True,
                "providers": ["CPUExecutionProvider"],
                "threads": 2,
            },
        )

        module.FastEmbedEmbedding(config)

        assert captured == {
            "model_name": "sentence-transformers/all-MiniLM-L6-v2",
            "specific_model_path": "D:/fixed/local/snapshot",
            "local_files_only": True,
            "providers": ["CPUExecutionProvider"],
            "threads": 2,
        }
    finally:
        for name in set(sys.modules) - imported_before:
            if name == "mem0" or name.startswith("mem0."):
                sys.modules.pop(name, None)
