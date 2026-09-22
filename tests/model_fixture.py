"""In-process provider transport for Assistant policy tests using mocked HTTP."""
from plugins.builtin.sakura_model_openai_compatible.transport import execute


class LocalModelClient:
    def complete(self, request, *, cancel_checker=None):
        return execute({"base_url": "https://api.example.com/v1", "api_key": "key", "model": "model", "timeout_seconds": 5},
                       request, cancel_checker=cancel_checker, progress=lambda _event: None)

    def close(self):
        pass
