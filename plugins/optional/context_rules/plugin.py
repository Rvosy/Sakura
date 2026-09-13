from __future__ import annotations

from collections.abc import Mapping
from typing import Any


DEFAULT_RULES = "用简短、自然的句子回答。先回答用户的问题；用户要求详细解释时再展开。"
MAX_TEXT_LENGTH = 4096
SETTINGS_SECTION_ID = "context_rules"


class ContextRulesPlugin:
    """User-authored context through ordinary Plugin API v4 Host Services."""

    def setup(self, context: Any) -> None:
        contributions = context.get("sakura.host.context")
        _require_instruction_context(contributions)
        config = context.config
        values = _config_values(config.get())

        def reconfigure(updated: Mapping[str, Any]) -> str:
            nonlocal values
            values = _config_values(updated)
            return "applied"

        def collect(_request: Mapping[str, Any]) -> list[dict[str, Any]]:
            current = values
            fragments: list[dict[str, Any]] = []
            if current["rules"].strip():
                fragments.append({
                    "id": "rules",
                    "kind": "instruction",
                    "content": current["rules"],
                    "required": True,
                    "budgetHint": 4096,
                })
            if current["reference"].strip():
                fragments.append({
                    "id": "reference",
                    "kind": "data",
                    "content": current["reference"],
                    "budgetHint": 2048,
                })
            return fragments

        def save(updated: Mapping[str, Any]) -> object:
            normalized = _config_values({**config.get(), **updated})
            return config.update({key: normalized[key] for key in updated if key in normalized})

        config.on_change(reconfigure)
        contributions.register(
            {
                "providerId": context.plugin_id,
                "description": "用户设置的对话行为规则和参考资料。",
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
                        "key": "rules",
                        "label": "行为规则",
                        "type": "string",
                        "default": DEFAULT_RULES,
                        "maxLength": MAX_TEXT_LENGTH,
                    },
                    {
                        "key": "reference",
                        "label": "参考资料",
                        "type": "string",
                        "default": "",
                        "maxLength": MAX_TEXT_LENGTH,
                    },
                ],
            },
            load=lambda: _config_values(config.get()),
            save=save,
        )


def _require_instruction_context(service: Any) -> None:
    describe = getattr(service, "describe", None)
    capabilities = describe() if callable(describe) else None
    if not (
        isinstance(capabilities, Mapping)
        and capabilities.get("schemaVersion") == 1
        and isinstance(capabilities.get("fragmentKinds"), list)
        and {"instruction", "data"}.issubset(capabilities["fragmentKinds"])
        and isinstance(capabilities.get("scopes"), list)
        and "turn" in capabilities["scopes"]
        and isinstance(capabilities.get("failurePolicies"), list)
        and "abort" in capabilities["failurePolicies"]
    ):
        raise RuntimeError(
            "CONTEXT_RULES_HOST_UNSUPPORTED: 当前 Sakura 不支持对话行为规则，请使用支持此功能的构建。"
        )


def _config_values(raw: Mapping[str, Any]) -> dict[str, str]:
    values = {
        "rules": raw.get("rules", DEFAULT_RULES),
        "reference": raw.get("reference", ""),
    }
    if any(not isinstance(value, str) or len(value) > MAX_TEXT_LENGTH for value in values.values()):
        raise ValueError("CONTEXT_RULES_CONFIG_INVALID")
    return values
