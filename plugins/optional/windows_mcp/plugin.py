from __future__ import annotations

import base64
import importlib.util
import json
from pathlib import Path
import re
import sys
import threading
import time


class WindowsMCPPlugin:
    """A Windows service plugin consuming only the public MCP component API."""

    def setup(self, context):
        if sys.platform != "win32":
            raise RuntimeError("WINDOWS_MCP_REQUIRES_WINDOWS")
        self.context = context
        self.mcp = context.get("sakura.mcp")
        self.artifacts = context.get("sakura.host.artifacts")
        self.stopping = threading.Event()
        self.lock = threading.RLock()
        self.state = "connecting"
        self.reason = ""
        self.discovered = {}
        self.handle = None
        self.worker = None
        context.effect(self.close)
        context.provide("sakura.windows-mcp", self,
                        exports=["status", "catalog", "begin", "inspect", "readResult", "cancel", "release"])
        spec = importlib.util.find_spec("windows_mcp")
        if spec is None or not spec.submodule_search_locations:
            raise RuntimeError("WINDOWS_MCP_DEPENDENCIES_MISSING")
        dependencies = str(Path(next(iter(spec.submodule_search_locations))).parent)
        work = context.data_path("server")
        work.mkdir(parents=True, exist_ok=True)
        server_config = work / "config.toml"
        if not server_config.exists():
            server_config.write_text("# Sakura plugin-owned Windows-MCP configuration\n", encoding="utf-8")
        self.handle = self.mcp.registerServer({
            "command": sys.executable,
            "args": ["-I", "-S", str(Path(__file__).with_name("server.py")), dependencies, str(server_config)],
            "cwd": str(work),
            "env": {"ANONYMIZED_TELEMETRY": "false", "POSTHOG_API_KEY": ""},
            "connectTimeout": 60, "requestTimeout": 100,
        })["handle"]
        context.get("sakura.host.tools").register({
            "name": "windows_mcp_result", "description": "读取、取消或释放 Windows 操作返回的大结果；offset 按字符计，读完后 release。",
            "parameters": {"type": "object", "properties": {"operationId": {"type": "string"},
                "action": {"type": "string", "enum": ["inspect", "read", "cancel", "release"]},
                "offset": {"type": "integer", "minimum": 0}}, "required": ["operationId", "action"]},
            "risk": "low",
        }, self.result_tool)
        self.worker = threading.Thread(target=self._discover, name="windows-mcp-discovery", daemon=True)
        self.worker.start()

    def status(self):
        with self.lock:
            return {"state": self.state, "reasonCode": self.reason, "toolCount": len(self.discovered)}

    def catalog(self):
        with self.lock:
            return list(self.discovered.values())

    def _discover(self):
        registrations = []
        try:
            cursor = None
            catalog = {}
            while True:
                operation = self.mcp.begin(self.handle, "tools/list", {"cursor": cursor} if cursor else {})["operationId"]
                try:
                    result = self._wait(operation)
                finally:
                    self.mcp.release(operation)
                for tool in result["tools"]:
                    catalog[tool["name"]] = tool
                cursor = result.get("nextCursor")
                if not cursor:
                    break
            aliases = set()
            for name, tool in catalog.items():
                if self.stopping.is_set():
                    return
                alias = "windows_mcp_" + re.sub(r"[^a-z0-9_]", "_", name.lower())
                if alias in aliases or len(alias) > 64:
                    raise RuntimeError("WINDOWS_MCP_TOOL_NAME_CONFLICT")
                aliases.add(alias)
                registrations.append(self.context.get("sakura.host.tools").register({
                    "name": alias, "description": (tool.get("description") or name)[:500],
                    "parameters": tool["inputSchema"], "timeoutSeconds": 110,
                    "risk": "low" if name in {"Snapshot", "Screenshot", "Wait"} else "high",
                }, lambda args, name=name: self._tool(name, args)))
            with self.lock:
                self.discovered = catalog
                self.state = "ready"
        except Exception as error:
            for dispose in reversed(registrations):
                dispose()
            with self.lock:
                self.state = "error"
                self.reason = getattr(error, "code", "WINDOWS_MCP_DISCOVERY_FAILED")

    def _wait(self, operation):
        deadline = time.monotonic() + 105
        while not self.stopping.is_set():
            status = self.mcp.inspect(operation)
            if status["state"] == "completed":
                if status.get("length", 0) and status.get("result") is None:
                    chunks, offset = [], 0
                    while not self.stopping.is_set():
                        piece = self.mcp.readResult(operation, offset)
                        chunks.append(piece["text"])
                        offset = piece["nextOffset"]
                        if piece["done"]:
                            return json.loads("".join(chunks))
                    break
                return status["result"]
            if status["state"] in {"error", "cancelled"}:
                raise RuntimeError("WINDOWS_MCP_OPERATION_FAILED")
            if time.monotonic() >= deadline:
                self.mcp.cancel(operation)
                raise TimeoutError("WINDOWS_MCP_OPERATION_TIMEOUT")
            self.stopping.wait(0.05)
        raise RuntimeError("WINDOWS_MCP_CLOSED")

    def begin(self, name, arguments):
        with self.lock:
            if name not in self.discovered:
                raise ValueError("WINDOWS_MCP_TOOL_NOT_FOUND")
        return self.mcp.begin(self.handle, "tools/call", {"name": name, "arguments": arguments})

    def inspect(self, operation):
        return self.mcp.inspect(operation)

    def readResult(self, operation, offset=0):
        return self.mcp.readResult(operation, offset)

    def cancel(self, operation):
        return self.mcp.cancel(operation)

    def release(self, operation):
        return self.mcp.release(operation)

    def result_tool(self, args):
        action = args["action"]
        operation = args["operationId"]
        if action == "read":
            return self.readResult(operation, args.get("offset", 0))
        if action not in {"inspect", "cancel", "release"}:
            raise ValueError("WINDOWS_MCP_ACTION_INVALID")
        return getattr(self, action)(operation)

    def _tool(self, name, args):
        operation = self.begin(name, args)["operationId"]
        retain = False
        try:
            result = self._wait(operation)
            images = [item for item in result.get("content", []) if item.get("type") == "image"]
            without_images = {**result, "content": [item for item in result.get("content", []) if item.get("type") != "image"]}
            if len(images) > 1 or len(json.dumps(without_images).encode("utf-8")) > 48000:
                retain = True
                return {"operationId": operation, "state": "completed", "readWith": "windows_mcp_result"}
            if not images:
                return result
            image = images[0]
            allocation = self.artifacts.allocate({"mediaType": image["mimeType"], "suffix": ".img"})
            try:
                Path(allocation["path"]).write_bytes(base64.b64decode(image["data"], validate=True))
                descriptor = self.artifacts.commit(allocation["artifactId"])
            except Exception:
                self.artifacts.release(allocation["artifactId"])
                raise
            return {"content": without_images, "artifact": descriptor}
        finally:
            if not retain:
                self.mcp.release(operation)

    def close(self):
        self.stopping.set()
        if self.handle:
            self.mcp.unregisterServer(self.handle)
        if self.worker is not None:
            self.worker.join(timeout=0.5)
