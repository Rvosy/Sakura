from __future__ import annotations

import ast
import json
import shutil
from pathlib import Path
from threading import Event

import pytest

from app.config.core_config_reader import CoreConfigReader
from app.core_host.assistant_adapter import AssistantAdapter
from app.core_host.protocol import response
from app.core_host.server import HostConfig


FIXTURE_ROOT = (
    Path(__file__).parents[1] / "fixtures" / "runtime_v2" / "wp_3_01" / "ready"
)
REPO_ROOT = Path(__file__).parents[2]
PLANTED_API_KEY = "PLANTED_API_KEY_DO_NOT_LEAK"
PLANTED_ENDPOINT = "https://planted-endpoint.invalid/private-v1"
PLANTED_MODEL = "planted-private-model"
PLANTED_PROMPT = "PLANTED_PRIVATE_SYSTEM_PROMPT"
PLANTED_CREDENTIAL = "abcdeffedcba0123456789abcdef0123"
GENERIC_SERIALIZER_FILES = (
    "app/core_host/server.py",
    "app/core_host/provider_settings.py",
    "app/core_host/assistant_adapter.py",
    "app/config/provider_model_settings.py",
    "app/config/core_config_reader.py",
    "app/config/models.py",
    "app/config/character_loader.py",
    "app/llm/api_client.py",
)


def _fresh_secret_root(tmp_path: Path) -> Path:
    root = tmp_path / "private-absolute-app-root"
    shutil.copytree(FIXTURE_ROOT, root)
    (root / "data" / "config" / "api.yaml").write_text(
        f"""\
api_profiles:
  - id: planted
    alias: Planted
    base_url: {PLANTED_ENDPOINT}
    api_key: {PLANTED_API_KEY}
    models:
      - name: {PLANTED_MODEL}
model_slots:
  chat:
    profile_id: planted
    model: {PLANTED_MODEL}
""",
        encoding="utf-8",
    )
    (root / "characters" / "sakura" / "card.md").write_text(
        PLANTED_PROMPT,
        encoding="utf-8",
    )
    return root


def _call_name(node: ast.Call) -> str:
    function = node.func
    if isinstance(function, ast.Name):
        return function.id
    if isinstance(function, ast.Attribute):
        parts = [function.attr]
        value = function.value
        while isinstance(value, ast.Attribute):
            parts.append(value.attr)
            value = value.value
        if isinstance(value, ast.Name):
            parts.append(value.id)
        return ".".join(reversed(parts))
    return ""


def test_sensitive_dto_modules_reject_generic_object_serializers() -> None:
    violations: list[str] = []
    for relative_path in GENERIC_SERIALIZER_FILES:
        path = REPO_ROOT / relative_path
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative_path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "__dict__":
                violations.append(f"{relative_path}:{node.lineno}:__dict__")
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = {alias.name for alias in node.names}
                module = node.module if isinstance(node, ast.ImportFrom) else None
                if "pickle" in names or module == "pickle" or "asdict" in names:
                    violations.append(f"{relative_path}:{node.lineno}:{sorted(names)}")
            if not isinstance(node, ast.Call):
                continue
            name = _call_name(node)
            if name in {"asdict", "dataclasses.asdict", "vars", "pickle.dumps", "pickle.dump"}:
                violations.append(f"{relative_path}:{node.lineno}:{name}")
            if name in {"json.dump", "json.dumps"} and any(
                keyword.arg == "default" for keyword in node.keywords
            ):
                violations.append(f"{relative_path}:{node.lineno}:{name}(default=)")

    assert violations == []


def test_host_config_repr_excludes_generation_credential() -> None:
    config = HostConfig(
        app_root=Path("/isolated/not-read/secret-repr"),
        generation_id="generation",
        generation_credential=PLANTED_CREDENTIAL,
    )

    assert PLANTED_CREDENTIAL not in repr(config)


def test_reader_and_readiness_repr_hide_provider_secrets(tmp_path: Path) -> None:
    root = _fresh_secret_root(tmp_path)
    read_result = CoreConfigReader().read(root)
    readiness = AssistantAdapter(root).initialize(Event())

    for output in (repr(read_result), repr(readiness)):
        assert PLANTED_API_KEY not in output
        assert PLANTED_ENDPOINT not in output
        assert PLANTED_MODEL not in output
        assert PLANTED_PROMPT not in output
        assert str(root) not in output


def test_public_projection_contains_no_private_provider_prompt_or_paths(tmp_path: Path) -> None:
    root = _fresh_secret_root(tmp_path)
    readiness = AssistantAdapter(root).initialize(Event())
    assert readiness.current_character_summary is not None
    serialized = json.dumps(readiness.current_character_summary, ensure_ascii=False)

    assert set(readiness.current_character_summary) == {
        "id",
        "displayName",
        "initialMessage",
        "replyTones",
        "portraitChoices",
    }
    for secret in (
        PLANTED_API_KEY,
        PLANTED_ENDPOINT,
        PLANTED_MODEL,
        PLANTED_PROMPT,
        PLANTED_CREDENTIAL,
        str(root),
    ):
        assert secret not in serialized


def test_sanitized_character_issue_and_failure_surfaces_do_not_leak(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _fresh_secret_root(tmp_path)
    private_package = root / "characters" / "private-broken"
    private_package.mkdir()
    (private_package / "character.json").write_text(
        json.dumps(
            {
                "id": "private-broken",
                "display_name": PLANTED_ENDPOINT,
                "initial_message": PLANTED_API_KEY,
                "card": PLANTED_PROMPT,
            }
        ),
        encoding="utf-8",
    )

    readiness = AssistantAdapter(root).initialize(Event())
    observed = "\n".join((repr(readiness), readiness.message, capsys.readouterr().err))

    for secret in (
        PLANTED_API_KEY,
        PLANTED_ENDPOINT,
        PLANTED_MODEL,
        PLANTED_PROMPT,
        PLANTED_CREDENTIAL,
        str(root),
    ):
        assert secret not in observed


def test_api_key_has_no_output_projector_and_credential_uses_existing_envelope_only() -> None:
    adapter_source = (REPO_ROOT / "app/core_host/assistant_adapter.py").read_text(encoding="utf-8")
    assert "apiKey" not in adapter_source
    assert "api_key" not in adapter_source
    assert adapter_source.count("def project_") == 1

    envelope = response(
        {"id": "request", "name": "system.health"},
        generation_id="generation",
        generation_credential=PLANTED_CREDENTIAL,
        payload={"readiness": "ready"},
    )
    assert envelope["generationCredential"] == PLANTED_CREDENTIAL
    assert PLANTED_CREDENTIAL not in json.dumps(envelope["payload"])

    serializer_sites: list[str] = []
    for relative_path in ("app/core_host/protocol.py", "app/core_host/server.py"):
        source = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
        if '"generationCredential":' in source:
            serializer_sites.append(relative_path)
    assert serializer_sites == ["app/core_host/protocol.py"]
