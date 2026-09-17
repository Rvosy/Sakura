from __future__ import annotations

from sakura_assistant.agent.screen_observation import (
    MANUAL_SCREEN_OBSERVATION_HISTORY_MARKER,
    SCREEN_OBSERVATION_HISTORY_MARKER,
)


class ScreenPolicy:
    """集中维护 Agent 屏幕观察入口策略。"""

    @staticmethod
    def should_offer_screen_observation_text(text: str | None) -> bool:
        """只在当前轮仍有可关联用户消息时开放自主屏幕观察。"""

        if text is None:
            return False
        return (
            SCREEN_OBSERVATION_HISTORY_MARKER not in text
            and MANUAL_SCREEN_OBSERVATION_HISTORY_MARKER not in text
        )
