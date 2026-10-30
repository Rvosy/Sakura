"""模型用量的功能分类，插件可使用自己的稳定分类 ID。"""

CATEGORIES = {
    "conversation": "对话",
    "memory_curation": "记忆整理",
    "vision": "视觉分析",
    "proactive": "主动回复",
    "background": "后台任务",
    "other": "其他功能",
    "uncategorized": "未分类",
}


def category_for_purpose(purpose):
    return {
        "agent_step": "conversation", "Agent 步骤": "conversation",
        "final_reply": "conversation", "最终回复": "conversation",
        "reply_repair": "conversation", "回复格式修复": "conversation",
        "memory_curation": "memory_curation", "memory_curation_repair": "memory_curation",
        "记忆整理": "memory_curation", "记忆整理修复": "memory_curation",
        "screen_observation": "vision", "屏幕观察": "vision",
        "proactive_reply": "proactive", "主动回复": "proactive",
        "background_agent": "background", "后台 Agent": "background",
    }.get(purpose, "uncategorized")
