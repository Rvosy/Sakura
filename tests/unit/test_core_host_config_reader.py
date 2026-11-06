import pytest

from app.config.core_config_reader import CoreConfigReader


def test_invalid_provider_configuration_does_not_block_core_or_get_rewritten(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    (config / "characters.yaml").write_text("current_character_id: sakura\n", encoding="utf-8")
    api = config / "api.yaml"
    api.write_text("invalid: [ private-key", encoding="utf-8")
    result = CoreConfigReader().read(tmp_path)
    assert result.config_problem is None
    assert result.current_character_id == "sakura"
    assert api.read_text(encoding="utf-8") == "invalid: [ private-key"


@pytest.mark.parametrize(("filename", "text", "code"), [
    ("system_config.yaml", "config_version: [", "CONFIG_DATA_INVALID"),
    ("system_config.yaml", "config_version: 2", "CONFIG_VERSION_UNSUPPORTED"),
    ("system_config.yaml", "config_version: true", "CONFIG_VERSION_UNSUPPORTED"),
    ("characters.yaml", "current_character_id: [", "CONFIG_DATA_INVALID"),
    ("characters.yaml", "current_character_id: 42", "CONFIG_DATA_INVALID"),
])
def test_core_owned_invalid_configuration_has_stable_errors(tmp_path, filename, text, code):
    config = tmp_path / "config"
    config.mkdir()
    path = config / filename
    path.write_text(text, encoding="utf-8")
    result = CoreConfigReader().read(tmp_path)
    assert result.config_problem.code == code
    assert result.config_problem.state == "failed"
    assert not result.config_problem.retryable
    assert path.read_text(encoding="utf-8") == text


@pytest.mark.parametrize("text", [None, "", "current_character_id: ''\n"])
def test_unselected_character_is_ready_without_model_provider(tmp_path, text):
    if text is not None:
        (tmp_path / "config").mkdir()
        (tmp_path / "config" / "characters.yaml").write_text(text, encoding="utf-8")
    result = CoreConfigReader().read(tmp_path)
    assert result.config_problem is None
    assert result.current_character_id is None
