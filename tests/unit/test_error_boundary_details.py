from app.core.diagnostics import exception_diagnostics
from app.core_host.assistant_adapter import AssistantFailure
from app.legacy_import.errors import LegacyImportError
from sakura_provider_errors import provider_exception_diagnostics, sanitize_provider_diagnostic


def test_migration_public_error_keeps_parser_cause_and_location():
    try:
        try:
            raise ValueError('C:\\旧数据\\config.json: unexpected token api_key=private-key')
        except ValueError as cause:
            raise LegacyImportError('LEGACY_CONFIG_INVALID', 'config', 'config.json', 7) from cause
    except LegacyImportError as error:
        result = error.to_public_dict()
    assert result['code'] == 'LEGACY_CONFIG_INVALID'
    assert result['line'] == 7
    assert 'unexpected token' in result['diagnostic']
    assert 'C:\\旧数据\\config.json' in result['exception_stack']
    assert 'private-key' not in str(result)


def test_provider_worker_diagnostics_survive_assistant_and_core_boundaries():
    try:
        raise OSError('C:\\runtime\\model.json: connection reset api_key=private-key')
    except OSError as error:
        details = provider_exception_diagnostics(error)
    failure = AssistantFailure({'code': 'PROVIDER_REQUEST_FAILED', 'message': '模型请求失败。',
                                'retryable': True, 'diagnostics': details})
    result = exception_diagnostics(failure, reason_code=failure.code, stage='chat')
    assert 'connection reset' in result['diagnostic']
    assert 'C:\\runtime\\model.json' in result['exception_stack']
    assert 'private-key' not in str(result)


def test_provider_text_preserves_paths_and_url_but_removes_url_credentials():
    result = sanitize_provider_diagnostic('C:\\runtime\\model.json\nhttps://user:password@example.test/v1 failed')
    assert 'C:\\runtime\\model.json\n' in result
    assert 'example.test/v1 failed' in result
    assert 'user:password' not in result


import pytest

@pytest.mark.parametrize('text', ['{"api_key": "fixture credential"}', "password='fixture credential'", 'token=fixture-credential'])
def test_provider_diagnostics_remove_quoted_credentials(text):
    assert 'fixture' not in sanitize_provider_diagnostic(text)

@pytest.mark.parametrize('stage', ['begin', 'poll', 'rpc'])
def test_tts_worker_error_reaches_core_response_with_original_diagnostics(stage):
    from types import SimpleNamespace
    from plugins.builtin.sakura_tts_hub.plugin import SakuraTTSHub
    from app.core_host.tts_boundary import _PluginSynthesisHandle, TTSBoundaryError
    from sakura_provider_errors import provider_failure
    from app.plugins.sakura_plugin_sdk import PluginApiError

    def fail_at_source():
        raise ValueError('角色尚未配置GPT 模型 /tmp/voice/model.ckpt api_key=private-key')
    try:
        fail_at_source()
    except ValueError as error:
        failure = provider_failure('TTS_CHARACTER_CONFIG_INVALID', error)
    if stage == 'rpc':
        remote = PluginApiError('PLUGIN_CALL_FAILED', diagnostics=failure['diagnostics'])
        failure = provider_failure('TTS_CHARACTER_CONFIG_INVALID', remote)
    if stage == 'poll':
        result = SakuraTTSHub._normalize_poll('request', 'provider', {'state': 'failed', 'elapsedMs': 100, **failure})
    else:
        class Config:
            def get(self):
                return {'selections': {'character': {'enabled': True, 'provider': 'provider'}}}
        hub = SakuraTTSHub(SimpleNamespace(bind=lambda _: SimpleNamespace(begin=lambda _: failure)), Config())
        hub.registerProvider({'providerId': 'provider', 'serviceKey': 'provider.service', 'label': 'Provider'})
        result = hub.begin({'requestId': 'request', 'characterId': 'character', 'text': 'test', 'options': {}})
    try:
        _PluginSynthesisHandle._raise_failed(result['errorCode'], result.get('diagnostics'))
    except TTSBoundaryError as error:
        details = exception_diagnostics(error, reason_code=error.code, stage='tts.history.prepare')
    assert '角色尚未配置GPT 模型' in details['diagnostic']
    assert '/tmp/voice/model.ckpt' in details['diagnostic']
    assert 'fail_at_source' in details['exception_stack']
    assert details['cause_type'] == 'ValueError'
    assert details['cause_code'] == 'TTS_CHARACTER_CONFIG_INVALID'
    assert 'fail_at_source' in details['exception_site']
    assert 'private-key' not in str(details)


def test_asr_hub_and_core_keep_worker_diagnostics():
    from plugins.builtin.sakura_asr_hub.plugin import SakuraASRHub
    from app.core_host.audio_input import AudioInputError
    from sakura_provider_errors import provider_failure
    try:
        raise OSError('model.onnx: incompatible tensor shape')
    except OSError as error:
        failure = provider_failure('ASR_RECOGNITION_FAILED', error)
    result = SakuraASRHub._failed(failure['errorCode'], failure['diagnostics'])
    error = AudioInputError(result['errorCode'], result['diagnostics'])
    details = exception_diagnostics(error, reason_code=error.code, stage='asr.input.poll')
    assert details['diagnostic'] == 'model.onnx: incompatible tensor shape'
    assert details['cause_type'] == 'OSError'
    assert 'test_asr_hub_and_core_keep_worker_diagnostics' in details['exception_stack']


@pytest.mark.parametrize("operation", ["status", "warmup"])
def test_tts_status_failure_preserves_provider_exception(operation):
    from types import SimpleNamespace
    from plugins.builtin.sakura_tts_hub.plugin import SakuraTTSHub

    def failed_status():
        raise OSError("fixture runtime.dll could not load api_key=private-key")
    provider = SimpleNamespace(status=failed_status)
    config = SimpleNamespace(get=lambda: {"selections": {"fixture": {"enabled": True, "provider": "provider"}}})
    hub = SakuraTTSHub(SimpleNamespace(get=lambda _: provider), config)
    hub.registerProvider({"providerId": "provider", "serviceKey": "provider.service", "label": "Provider"})
    result = getattr(hub, operation)("fixture")
    assert "runtime.dll could not load" in result["diagnostics"]["diagnostic"]
    assert "failed_status" in result["diagnostics"]["exception_stack"]
    assert "private-key" not in str(result)


@pytest.mark.parametrize("kind", ["genie", "gpt_sovits"])
def test_provider_warmup_keeps_configuration_read_exception(tmp_path, kind):
    from types import SimpleNamespace
    from plugins.optional.sakura_genie.plugin import GenieProvider
    from plugins.optional.sakura_gpt_sovits.plugin import GPTSoVITSProvider

    def failed(*_args):
        raise PermissionError("fixture character.json access denied api_key=private-key")
    provider_type = GenieProvider if kind == "genie" else GPTSoVITSProvider
    provider = object.__new__(provider_type)
    (tmp_path / "api_v2.py").touch()
    python = tmp_path / "python.exe"
    python.touch()
    provider._config = SimpleNamespace(enabled=True, endpoint_mode="managed", custom_base_url=None, work_dir=tmp_path, python_path=python)
    provider._coordinator = object()
    provider._voice = failed
    provider._character = SimpleNamespace(get=failed)
    result = provider.warmup("fixture")
    assert not result["accepted"]
    assert "character.json access denied" in result["diagnostics"]["diagnostic"]
    assert "PermissionError" in result["diagnostics"]["exception_stack"]
    assert "private-key" not in str(result)


@pytest.mark.parametrize("kind", ["genie", "gpt_sovits"])
def test_provider_config_parse_failure_is_not_replaced_by_unavailable(tmp_path, monkeypatch, kind):
    from importlib import import_module
    from types import SimpleNamespace

    module = import_module(f"plugins.optional.sakura_{kind}.plugin")
    def broken_config(_values):
        raise ValueError("fixture provider config: invalid timeout api_key=private-key")
    monkeypatch.setattr(module, "_parse_config", broken_config)
    context = SimpleNamespace(config=SimpleNamespace(get=lambda: {}), data_path=lambda path: tmp_path / path)
    provider_type = module.GenieProvider if kind == "genie" else module.GPTSoVITSProvider
    provider = provider_type(context, object(), object())
    for result in (provider.status(), provider.warmup("fixture"), provider.begin({})):
        assert "invalid timeout" in result["diagnostics"]["diagnostic"]
        assert "broken_config" in result["diagnostics"]["exception_stack"]
        assert "private-key" not in str(result)
