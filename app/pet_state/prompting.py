from __future__ import annotations

import json
from typing import Any


def build_pet_state_context_message(snapshot: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(snapshot, dict):
        return None
    state = snapshot.get("state")
    if not isinstance(state, dict):
        return None
    payload = {
        "state": state,
        "last_model_delta": snapshot.get("last_model_delta"),
        "last_harness_decision": snapshot.get("last_harness_decision"),
    }
    content = (
        "宿主主动注入的桌宠状态 pet_state如下。它表示跨轮次稳定状态，不等同于本轮回复段落的 "
        "ChatSegment.tone 或 ChatSegment.portrait。\n"
        f"{json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n"
        "情绪模块已启用。长期状态的正式写路径是 pet_state_update；"
        "最终回复 JSON 不需要每次携带 pet_state_delta。"
        "当用户直接询问 Sakura 当前心情、状态或感觉如何时，先调用 pet_state_get 读取当前状态后再回答；"
        "如果只是查询且状态不变，不要调用 pet_state_update。"
        "如果本轮用户消息、运行事件或你的回复会让 Sakura 的跨轮次心情明显变化，"
        "应先调用 pet_state_update 提交 delta，再给出最终回复；"
        "如果状态没有变化，不要为了形式调用工具。"
        "pet_state_update.delta 只写 mood、affect、evidence；不要写 display，display 是宿主只读派生。"
        "如果最终回复同级出现 pet_state_delta，它只作为可选建议、debug 或审计信息，"
        "不能替代 pet_state_update，也不会作为正式落盘依据。"
        "不要在自然回复中复述这段上下文。"
    )
    return {"role": "system", "content": content}
