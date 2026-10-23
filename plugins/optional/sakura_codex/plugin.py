"""Codex 供应商只接入账号与协议差异，任务由公共 SDK 管理。"""
from sakura_model_provider import ModelProvider

if __package__:
    from .profiles import CodexProfiles, SERVICE_KEY
else:
    from profiles import CodexProfiles, SERVICE_KEY


class CodexPlugin(ModelProvider):
    def setup(self, context):
        profiles = CodexProfiles(context)
        super().setup(context, profiles=profiles, service_key=SERVICE_KEY, label="OpenAI Official")

    def execute(self, settings, request, **kwargs):
        if __package__:
            from .transport import execute
        else:
            from transport import execute
        return execute(self.profiles.account_for(settings["profileId"]), settings, request, **kwargs)

    def diagnostic_secrets(self, settings):
        tokens = settings["account"]["tokens"]
        return tuple(tokens.get(key, "") for key in ("access_token", "refresh_token", "id_token"))
