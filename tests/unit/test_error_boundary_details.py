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
