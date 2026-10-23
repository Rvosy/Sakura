"""OpenAI 兼容模型使用公共模型任务服务。"""
from sakura_model_provider import ModelProvider

if __package__:
    from .profiles import ProviderProfiles, SERVICE_KEY
else:
    from profiles import ProviderProfiles, SERVICE_KEY


class ModelPlugin(ModelProvider):
    def setup(self, context):
        super().setup(context, profiles=ProviderProfiles(context), service_key=SERVICE_KEY, label="OpenAI 兼容模型")

    def execute(self, settings, request, **kwargs):
        if __package__:
            from .transport import execute
        else:
            from transport import execute
        return execute(settings, request, **kwargs)
