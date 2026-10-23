"""插件处理钩子的公开契约与 JSON 传输。"""

from __future__ import annotations

import json
from pathlib import Path


HOOK_POINTS = {
    "chat.before_assistant": "修改本轮交给 Assistant 的消息，历史保留用户原文。",
    "chat.before_commit": "修改最终回复段落，修改结果用于历史、字幕和语音。",
    "model.before_request": "修改 ModelClient 发给模型的请求。",
    "model.after_response": "修改 ModelClient 返回给消费者的响应。",
    "tool.before_execute": "修改工具参数或阻止本次执行。",
    "tool.after_execute": "修改交给消费者的工具结果内容。",
}


class HookError(RuntimeError):
    def __init__(self, code, message, *, hook="", plugin_id=""):
        super().__init__(message)
        self.code = code
        self.hook = hook
        self.plugin_id = plugin_id


def pack_hook_value(value, artifacts):
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(encoded) <= 32768:
        return {"value": value}
    allocation = artifacts.allocate({"mediaType": "application/json", "suffix": ".json"})
    try:
        Path(allocation["path"]).write_bytes(encoded)
        return {"artifact": artifacts.commit(allocation["artifactId"])}
    except BaseException:
        artifacts.release(allocation["artifactId"])
        raise


def unpack_hook_value(envelope, artifacts, *, release=True):
    """读取传输数据；请求输入由发送端清理，响应由接收端清理。"""
    if "value" in envelope:
        return envelope["value"]
    identity = envelope["artifact"]["artifactId"]
    artifact = artifacts.resolve(identity)
    try:
        return json.loads(Path(artifact["path"]).read_bytes())
    finally:
        if release:
            artifacts.release_received(identity)
