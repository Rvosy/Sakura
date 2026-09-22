from __future__ import annotations

from collections.abc import Mapping
from typing import Any


DEFAULT_CONTENT = "用简短、自然的句子回答。先回答用户的问题；用户要求详细解释时再展开。"
# Preserve both previously valid 4096-character fields and their separator.
MAX_TEXT_LENGTH = 4096 * 2 + 2
SETTINGS_SECTION_ID = "context_rules"


class ContextRulesPlugin:
    """User-authored context through ordinary Plugin API v4 Host Services."""

    def setup(self, context: Any) -> None:
        contributions = context.get("sakura.host.context")
        _require_content_context(contributions)
        config = context.config
        values = _config_values(config.get())

        def reconfigure(updated: Mapping[str, Any]) -> str:
            nonlocal values
            values = _config_values(updated)
            return "applied"

        def collect(_request: Mapping[str, Any]) -> list[dict[str, Any]]:
            content = values["content"]
            if not content.strip():
                return []
            return [{"id": "content", "content": content, "required": True}]

        def save(updated: Mapping[str, Any]) -> object:
            normalized = _config_values({**config.get(), **updated})
            return config.update({key: normalized[key] for key in updated if key in normalized})

        config.on_change(reconfigure)
        contributions.register(
            {
                "providerId": context.plugin_id,
                "description": "用户填写的对话上下文。",
                "order": 50,
                "scope": "turn",
                "failurePolicy": "abort",
            },
            collect,
        )
        context.get("sakura.host.settings").register(
            {
                "sectionId": SETTINGS_SECTION_ID,
                "title": "对话规则",
                "order": 50,
                "fields": [
                    {
                        "key": "content",
                        "label": "上下文",
                        "type": "string",
                        "default": DEFAULT_CONTENT,
                        "maxLength": MAX_TEXT_LENGTH,
                    },
                ],
            },
            load=lambda: _config_values(config.get()),
            save=save,
        )


def _require_content_context(service: Any) -> None:
    describe = getattr(service, "describe", None)
    capabilities = describe() if callable(describe) else None
    if not (
        isinstance(capabilities, Mapping)
        and capabilities.get("schemaVersion") == 2
        and isinstance(capabilities.get("scopes"), list)
        and "turn" in capabilities["scopes"]
        and isinstance(capabilities.get("failurePolicies"), list)
        and "abort" in capabilities["failurePolicies"]
    ):
        raise RuntimeError(
            "CONTEXT_RULES_HOST_UNSUPPORTED: 当前 Sakura 不支持此上下文接口，请更新应用。"
        )


def _config_values(raw: Mapping[str, Any]) -> dict[str, str]:
    if "content" in raw:
        content = raw["content"]
    else:
        previous = [raw.get("rules", DEFAULT_CONTENT), raw.get("reference", "")]
        if any(not isinstance(value, str) for value in previous):
            raise ValueError("CONTEXT_RULES_CONFIG_INVALID")
        content = "\n\n".join(value for value in previous if value != "")
    if not isinstance(content, str) or len(content) > MAX_TEXT_LENGTH:
        raise ValueError("CONTEXT_RULES_CONFIG_INVALID")
    return {"content": content}
