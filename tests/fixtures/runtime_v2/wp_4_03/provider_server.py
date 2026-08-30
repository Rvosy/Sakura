from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port-file", type=Path, required=True)
    parser.add_argument("--tool-sentinel", required=True)
    return parser.parse_args()


def _reply_content(text: str) -> str:
    return json.dumps(
        {
            "segments": [
                {
                    "ja": text,
                    "zh": text,
                    "tone": "neutral",
                    "portrait": "neutral",
                }
            ]
        },
        ensure_ascii=False,
    )


def _tool_name(payload: dict[str, Any]) -> str:
    for tool in payload.get("tools", []):
        function = tool.get("function", {}) if isinstance(tool, dict) else {}
        name = function.get("name") if isinstance(function, dict) else None
        if isinstance(name, str) and name.endswith("fixture_echo"):
            return name
    return "fixture__fixture_echo"


def _user_text(payload: dict[str, Any]) -> str:
    values: list[str] = []
    for message in payload.get("messages", []):
        if isinstance(message, dict) and message.get("role") == "user":
            content = message.get("content")
            if isinstance(content, str):
                values.append(content)
    return "\n".join(values)


class ProviderHandler(BaseHTTPRequestHandler):
    tool_sentinel = ""

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length))
        messages = payload.get("messages", [])
        last_role = messages[-1].get("role") if messages and isinstance(messages[-1], dict) else ""
        if last_role == "tool":
            message = {
                "role": "assistant",
                "content": _reply_content("MCP 工具调用已完成。"),
            }
        else:
            delay = 5 if "超时" in _user_text(payload) else 0
            message = {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "wp403_fixture_call",
                        "type": "function",
                        "function": {
                            "name": _tool_name(payload),
                            "arguments": json.dumps(
                                {
                                    "value": type(self).tool_sentinel,
                                    "delay_seconds": delay,
                                }
                            ),
                        },
                    }
                ],
            }
        body = json.dumps({"choices": [{"message": message}]}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return None


def main() -> None:
    arguments = _arguments()
    ProviderHandler.tool_sentinel = arguments.tool_sentinel
    server = ThreadingHTTPServer(("127.0.0.1", 0), ProviderHandler)
    server.daemon_threads = True
    arguments.port_file.write_text(str(server.server_address[1]), encoding="ascii")
    server.serve_forever()


if __name__ == "__main__":
    main()
