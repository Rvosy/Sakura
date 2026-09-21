"""SDK-backed MCP connections. Every SDK context is entered/exited by its owner task."""
from __future__ import annotations

import asyncio
from collections import deque
from contextlib import AsyncExitStack, contextmanager
from dataclasses import asdict, is_dataclass
import json
import math
import os
import tempfile
import threading
import uuid
from urllib.parse import urlsplit

import httpx2
from mcp import Client, StdioServerParameters, types
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from pydantic import TypeAdapter


CALL_TIMEOUT_SECONDS = 10
CLOSE_TIMEOUT_SECONDS = 10


class MCPComponentError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def wire(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True, exclude_none=True)
    if is_dataclass(value):
        return {key: wire(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {key: wire(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [wire(item) for item in value]
    return value


def configuration(raw):
    if not isinstance(raw, dict):
        raise MCPComponentError("MCP_CONFIG_INVALID")
    value = json.loads(json.dumps(raw, allow_nan=False))
    transport = value.setdefault("transport", value.get("type", "stdio" if "command" in value else "streamable-http"))
    transport = value["transport"] = {"http": "streamable-http", "streamableHttp": "streamable-http"}.get(transport, transport)
    if transport not in {"stdio", "streamable-http", "sse"}:
        raise MCPComponentError("MCP_TRANSPORT_INVALID")
    for key, default in (("connectTimeout", 60), ("requestTimeout", 300)):
        number = value.setdefault(key, default)
        if isinstance(number, bool) or not isinstance(number, (float, int)) or not math.isfinite(number) or number <= 0:
            raise MCPComponentError("MCP_TIMEOUT_INVALID")
    for key in ("env", "headers"):
        items = value.get(key, {})
        if not isinstance(items, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in items.items()):
            raise MCPComponentError("MCP_CONFIG_INVALID")
    if transport == "stdio":
        if not isinstance(value.get("command"), str) or not value["command"].strip():
            raise MCPComponentError("MCP_COMMAND_REQUIRED")
        if not isinstance(value.get("args", []), list) or any(not isinstance(v, str) for v in value.get("args", [])):
            raise MCPComponentError("MCP_CONFIG_INVALID")
        StdioServerParameters.model_validate({key: value[key] for key in ("command", "args", "env", "cwd", "encoding", "encoding_error_handler") if key in value})
    else:
        url = urlsplit(value.get("url", ""))
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.fragment:
            raise MCPComponentError("MCP_URL_INVALID")
    for key in ("sampling", "elicitation"):
        if key in value and not isinstance(value[key], bool):
            raise MCPComponentError("MCP_CONFIG_INVALID")
    if "roots" in value:
        types.ListRootsResult.model_validate({"roots": value["roots"]})
    if value.get("oauth") is not None and not isinstance(value["oauth"], (dict, bool)):
        raise MCPComponentError("MCP_CONFIG_INVALID")
    if "extensions" in value and (not isinstance(value["extensions"], dict) or any(not isinstance(item, dict) for item in value["extensions"].values())):
        raise MCPComponentError("MCP_CONFIG_INVALID")
    return value


class Component:
    def __init__(self, auth_path=None, logger=None):
        self.auth_path = auth_path
        self.logger = logger
        self.loop = asyncio.new_event_loop()
        self.connections = {}
        self.operations = {}
        self.revoked = set()
        self._revocations = {}
        self.closed = False
        self.closing = False
        self.failure = None
        self._calls = set()
        self._shutdown = None
        self._lifecycle_lock = threading.Lock()
        self.thread = threading.Thread(target=self._run, name="mcp-client", daemon=True)
        self.thread.start()

    def _log(self, level, message, conn, **fields):
        if self.logger is not None:
            getattr(self.logger, level)(message, fields={
                "consumerPluginId": conn["owner"][0], "connectionId": conn["handle"],
                "transport": conn["config"]["transport"], **fields})

    @contextmanager
    def _stderr(self, conn):
        reader_fd, writer_fd = os.pipe()
        reader = os.fdopen(reader_fd, "r", encoding="utf-8", errors="replace")
        writer = os.fdopen(writer_fd, "w", encoding="utf-8")
        def drain():
            with reader:
                while chunk := reader.readline(4096):
                    self._log("info", "MCP 服务端诊断输出", conn, output=chunk.rstrip())
        worker = threading.Thread(target=drain, name="mcp-stderr", daemon=True)
        worker.start()
        try:
            yield writer
        finally:
            writer.close()
            worker.join(timeout=1)

    def _run(self):
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_forever()
            self.loop.run_until_complete(self.loop.shutdown_asyncgens())
        finally:
            self.loop.close()
            self.closed = True

    def call(self, method, *args):
        with self._lifecycle_lock:
            if self.closed or self.closing:
                raise MCPComponentError("MCP_COMPONENT_CLOSED" if self.closed else "MCP_COMPONENT_CLOSING")
            if self.failure:
                raise MCPComponentError(self.failure)
            future = asyncio.run_coroutine_threadsafe(getattr(self, method)(*args), self.loop)
            self._calls.add(future)
        future.add_done_callback(self._call_finished)
        try:
            return future.result(timeout=CALL_TIMEOUT_SECONDS)
        except TimeoutError as error:
            if future.done():
                raise
            # A timed-out control call can still own a connection or task.
            # Keep that future for close; never replace the loop or replay it.
            with self._lifecycle_lock:
                self.failure = "MCP_COMPONENT_CALL_TIMEOUT"
            raise MCPComponentError(self.failure) from error

    def _call_finished(self, future):
        with self._lifecycle_lock:
            self._calls.discard(future)

    async def register(self, owner, raw, label="", credential_key=None):
        if owner in self.revoked:
            raise MCPComponentError("MCP_SCOPE_CLOSED")
        config = configuration(raw)
        handle = uuid.uuid4().hex
        conn = {"handle": handle, "owner": owner, "config": config, "label": label,
                "state": "connecting", "ready": asyncio.Event(), "stop": asyncio.Event(),
                "events": deque(maxlen=128), "sequence": 0, "pending": {}, "client": None,
                "credentialKey": credential_key, "removed": False}
        previous = [item["task"] for item in self.connections.values()
                    if credential_key and item["credentialKey"] == credential_key]
        if any(credential_key and item["credentialKey"] == credential_key and not item["removed"]
               for item in self.connections.values()):
            raise MCPComponentError("MCP_CREDENTIAL_IN_USE")
        self.connections[handle] = conn
        conn["task"] = asyncio.create_task(self._connection(conn, credential_key, previous))
        conn["task"].add_done_callback(lambda _: self.connections.pop(handle, None) if conn["removed"] else None)
        return {"handle": handle}

    def _owned(self, source, owner, handle):
        value = source.get(handle)
        if value is None or value["owner"] != owner:
            raise MCPComponentError("MCP_HANDLE_NOT_FOUND")
        return value

    def _event(self, conn, kind, payload):
        conn["sequence"] += 1
        conn["events"].append({"sequence": conn["sequence"], "type": kind, "data": wire(payload)})

    def _authorization(self, conn, url):
        conn["authorizationUrl"] = url
        if url:
            self._event(conn, "authorization", {"url": url})

    async def authorizations(self, owner):
        return [conn["authorizationUrl"] for conn in self.connections.values()
                if conn["owner"] == owner and conn.get("authorizationUrl") and conn["state"] in {"connecting", "ready"}]

    async def _connection(self, conn, credential_key, previous):
        config = conn["config"]
        self._log("info", "MCP 服务连接中", conn)
        try:
            if previous:
                await asyncio.gather(*previous, return_exceptions=True)
            async with AsyncExitStack() as stack:
                if config["transport"] == "stdio":
                    params = StdioServerParameters.model_validate({key: config[key] for key in ("command", "args", "env", "cwd", "encoding", "encoding_error_handler") if key in config})
                    # Host logging applies bounded queues, redaction and plugin attribution.
                    errlog = stack.enter_context(self._stderr(conn))
                    transport = stdio_client(params, errlog=errlog)
                else:
                    auth = None
                    if config.get("oauth") is not None and config.get("oauth") is not False:
                        try:
                            from .oauth import authorization
                        except ImportError:
                            from oauth import authorization
                        auth = await stack.enter_async_context(authorization(
                            config, self.auth_path(credential_key) if self.auth_path and credential_key else None,
                            lambda url: self._authorization(conn, url),
                        ))
                    options = {"headers": config.get("headers", {}), "auth": auth,
                               "timeout": httpx2.Timeout(config["connectTimeout"], read=config["requestTimeout"])}
                    if "proxy" in config:
                        options["proxy"] = config["proxy"]
                    if config["transport"] == "sse":
                        transport = sse_client(config["url"], headers=options["headers"], auth=auth,
                            timeout=config["connectTimeout"], sse_read_timeout=config["requestTimeout"],
                            httpx_client_factory=lambda **kwargs: httpx2.AsyncClient(**{**options, **kwargs}))
                    else:
                        http = await stack.enter_async_context(httpx2.AsyncClient(**options))
                        transport = streamable_http_client(config["url"], http_client=http)

                async def elicitation(context, params):
                    return types.ElicitResult.model_validate(await self._input(conn, "elicitation", wire(params)))

                async def sampling(context, params):
                    result = await self._input(conn, "sampling", wire(params))
                    return TypeAdapter(types.CreateMessageResult | types.CreateMessageResultWithTools | types.ErrorData).validate_python(result)

                async def roots(context):
                    return types.ListRootsResult.model_validate({"roots": config["roots"]})

                async def message(message):
                    if isinstance(message, Exception):
                        self._log("error", "MCP 传输失败", conn, reason_code="MCP_TRANSPORT_FAILED",
                                  error_type=type(message).__name__, diagnostic=str(message))
                        conn.update(state="error", error="MCP_TRANSPORT_FAILED")
                        self._event(conn, "transportError", {"code": "MCP_TRANSPORT_FAILED"})
                        conn["stop"].set()
                    else:
                        self._event(conn, "notification", message)

                async def logging(params):
                    level = {"debug": "debug", "warning": "warning", "error": "error",
                             "critical": "error", "alert": "error", "emergency": "error"}.get(params.level, "info")
                    self._log(level, "MCP 服务端日志", conn, output=wire(params.data))

                kwargs = {"message_handler": message, "mode": config.get("mode", "auto"),
                          "logging_callback": logging, "log_level": "info"}
                if config.get("extensions"):
                    from mcp.client.extension import advertise
                    kwargs["extensions"] = [advertise(key, value) for key, value in config["extensions"].items()]
                if config.get("elicitation"):
                    kwargs["elicitation_callback"] = elicitation
                if config.get("sampling"):
                    kwargs["sampling_callback"] = sampling
                if "roots" in config:
                    kwargs["list_roots_callback"] = roots
                client = Client(transport, **kwargs)
                # wait_for would enter the SDK's cancel scopes in another task.
                # A timer cancels this owner task only while connection is pending.
                task = asyncio.current_task()
                timer = self.loop.call_later(config["connectTimeout"], task.cancel)
                try:
                    await stack.enter_async_context(client)
                finally:
                    timer.cancel()
                conn.update(client=client, state="ready", protocolVersion=client.protocol_version,
                            serverInfo=wire(client.server_info), capabilities=wire(client.server_capabilities),
                            instructions=client.instructions)
                conn["authorizationUrl"] = None
                conn["ready"].set()
                self._log("info", "MCP 服务已连接", conn, protocolVersion=client.protocol_version)
                await conn["stop"].wait()
                await self._cancel_operations(conn["handle"])
        except asyncio.CancelledError:
            if conn["state"] == "connecting":
                conn["error"] = "MCP_CONNECT_TIMEOUT"
                self._log("error", "MCP 服务连接超时", conn, reason_code=conn["error"])
        except Exception:
            conn["error"] = "MCP_CONNECTION_FAILED"
            self._log("error", "MCP 服务连接失败", conn, reason_code=conn["error"])
        finally:
            conn["state"] = "error" if "error" in conn else "closing"
            conn["ready"].set()
            await self._cancel_operations(conn["handle"])
            conn["client"] = None
            conn["state"] = "error" if "error" in conn else "closed"
            if conn["state"] == "closed":
                self._log("info", "MCP 服务连接已关闭", conn)
            conn["ready"].set()
            for pending in list(conn["pending"].values()):
                pending["future"].cancel()
            conn["pending"].clear()
            if conn["removed"]:
                self.connections.pop(conn["handle"], None)

    async def status(self, owner):
        return [{key: conn[key] for key in ("handle", "label", "state", "error", "protocolVersion", "serverInfo", "capabilities", "instructions") if key in conn}
                for conn in self.connections.values() if conn["owner"] == owner]

    async def unregister(self, owner, handle):
        conn = self._owned(self.connections, owner, handle)
        conn["removed"] = True
        conn["stop"].set()
        if conn["state"] == "connecting":
            conn["state"] = "closing"
            conn["task"].cancel()
        # Return promptly. The owner task still owns and reaps all transport resources.
        conn["state"] = "closing"
        if conn["task"].done():
            self.connections.pop(handle, None)
        return {"state": "closing"}

    async def begin(self, owner, handle, method, params=None, options=None):
        conn = self._owned(self.connections, owner, handle)
        options = options or {}
        if not isinstance(options, dict) or set(options) - {"raw"} or ("raw" in options and not isinstance(options["raw"], bool)):
            raise MCPComponentError("MCP_REQUEST_OPTIONS_INVALID")
        if conn["state"] not in {"connecting", "ready"}:
            raise MCPComponentError("MCP_CONNECTION_NOT_READY")
        if not isinstance(method, str) or not method or not isinstance(params or {}, dict):
            raise MCPComponentError("MCP_REQUEST_INVALID")
        if sum(op["owner"] == owner for op in self.operations.values()) >= 128:
            raise MCPComponentError("MCP_OPERATIONS_FULL")
        op_id = uuid.uuid4().hex
        op = {"operationId": op_id, "owner": owner, "handle": handle, "state": "running", "result": None}
        self.operations[op_id] = op
        op["task"] = asyncio.create_task(self._operate(conn, op, method, params or {}, options))
        op["task"].add_done_callback(
            lambda task: op.update(state="cancelled") if task.cancelled() else None
        )
        return {"operationId": op_id, "state": "running"}

    async def _operate(self, conn, op, method, params, options):
        async def progress(progress, total=None, message=None):
            op["progress"] = {"progress": progress, "total": total, "message": message}
        try:
            async with asyncio.timeout(conn["config"]["requestTimeout"]):
                await conn["ready"].wait()
                client = conn["client"]
                if conn["state"] != "ready" or client is None:
                    raise MCPComponentError("MCP_CONNECTION_NOT_READY")
                if options.get("raw"):
                    result = await client.session.send_request(types.Request(method=method, params=params),
                        TypeAdapter(dict[str, object]), progress_callback=progress)
                elif method == "subscriptions/listen":
                    async with client.listen(**params) as subscription:
                        op["subscription"] = wire(subscription.honored)
                        async for event in subscription:
                            self._event(conn, "subscription", event)
                    result = {}
                elif method == "tools/call":
                    result = await client.call_tool(progress_callback=progress, **self._params(params))
                elif method in {"resources/read", "prompts/get"}:
                    callback = client.read_resource if method == "resources/read" else client.get_prompt
                    result = await callback(**self._params(params))
                elif method in {"tools/list", "resources/list", "resources/templates/list", "prompts/list"}:
                    callback = {"tools/list": client.list_tools, "resources/list": client.list_resources,
                                "resources/templates/list": client.list_resource_templates, "prompts/list": client.list_prompts}[method]
                    result = await callback(**self._params(params))
                else:
                    # Official low-level escape hatch preserves extension methods and JSON fields.
                    result = await client.session.send_request(types.Request(method=method, params=params),
                        TypeAdapter(dict[str, object]), progress_callback=progress)
                value = wire(result)
                data = json.dumps(value, ensure_ascii=False, allow_nan=False)
                op["length"] = len(data)
                if len(data.encode("utf-8")) <= 32768:
                    op["result"] = value
                else:
                    stream = tempfile.TemporaryFile(mode="w+t", encoding="utf-8", newline="")
                    stream.write(data)
                    stream.seek(0)
                    op["stream"] = stream
                op["state"] = "completed"
                if isinstance(value, dict) and value.get("isError") is True:
                    self._log("error", "MCP 工具返回失败", conn, operationId=op["operationId"], reason_code="MCP_TOOL_ERROR")
        except asyncio.CancelledError:
            op["state"] = "cancelled"
        except TimeoutError:
            op.update(state="error", error="MCP_REQUEST_TIMEOUT")
            self._log("error", "MCP 请求超时", conn, operationId=op["operationId"], reason_code=op["error"])
        except Exception as error:
            op.update(state="error", error=getattr(error, "code", "MCP_REQUEST_FAILED"))
            self._log("error", "MCP 请求失败", conn, operationId=op["operationId"], reason_code=op["error"])

    @staticmethod
    def _params(params):
        aliases = {"_meta": "meta", "inputResponses": "input_responses", "requestState": "request_state"}
        return {aliases.get(key, key): value for key, value in params.items()}

    async def inspect(self, owner, operation_id):
        op = self._owned(self.operations, owner, operation_id)
        return {key: op[key] for key in ("operationId", "handle", "state", "error", "progress", "subscription", "result", "length") if key in op}

    async def read_result(self, owner, operation_id, offset=0, limit=8192):
        op = self._owned(self.operations, owner, operation_id)
        if not isinstance(offset, int) or offset < 0 or not isinstance(limit, int) or not 1 <= limit <= 8192:
            raise MCPComponentError("MCP_RESULT_RANGE_INVALID")
        if op["state"] != "completed":
            raise MCPComponentError("MCP_RESULT_NOT_READY")
        if "stream" in op:
            # Text offsets are character offsets, not opaque UTF-8 byte positions.
            stream = op["stream"]
            stream.seek(0)
            remaining = offset
            while remaining:
                skipped = stream.read(min(remaining, 65536))
                if not skipped:
                    break
                remaining -= len(skipped)
            chunk = stream.read(limit)
        else:
            chunk = json.dumps(op["result"], ensure_ascii=False, allow_nan=False)[offset:offset + limit]
        return {"text": chunk, "nextOffset": offset + len(chunk), "done": offset + len(chunk) >= op["length"]}

    async def cancel(self, owner, operation_id):
        op = self._owned(self.operations, owner, operation_id)
        if not op["task"].done() and not op["task"].cancelling():
            op["task"].cancel()
        return {"operationId": operation_id}

    async def release(self, owner, operation_id):
        op = self._owned(self.operations, owner, operation_id)
        if not op["task"].done() and not op["task"].cancelling():
            op["task"].cancel()
        await asyncio.gather(op["task"], return_exceptions=True)
        # release() and scope revocation can await the same task concurrently.
        # Only the coroutine that removes its record owns the retained stream.
        if self.operations.pop(operation_id, None) is op and "stream" in op:
            op["stream"].close()
        return {}

    async def _cancel_operations(self, handle):
        tasks = [op["task"] for op in self.operations.values() if op["handle"] == handle and not op["task"].done()]
        for task in tasks:
            if not task.cancelling():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _input(self, conn, kind, params):
        request_id = uuid.uuid4().hex
        future = self.loop.create_future()
        conn["pending"][request_id] = {"type": kind, "params": params, "future": future}
        try:
            async with asyncio.timeout(conn["config"]["requestTimeout"]):
                return await future
        finally:
            conn["pending"].pop(request_id, None)

    async def events(self, owner, handle, after=0, pending_offset=0):
        conn = self._owned(self.connections, owner, handle)
        if not isinstance(after, int) or after < 0 or not isinstance(pending_offset, int) or pending_offset < 0:
            raise MCPComponentError("MCP_EVENT_CURSOR_INVALID")
        events = []
        for event in [item for item in conn["events"] if item["sequence"] > after][:16]:
            data = json.dumps(event["data"], ensure_ascii=False)
            events.append(event if len(data.encode("utf-8")) <= 2048 else {
                "sequence": event["sequence"], "type": event["type"], "dataLength": len(data)})
        pending = []
        items = list(conn["pending"].items())
        for key, value in items[pending_offset:pending_offset + 8]:
            data = json.dumps(value["params"], ensure_ascii=False)
            pending.append({"requestId": key, "type": value["type"],
                            **({"params": value["params"]} if len(data.encode("utf-8")) <= 2048 else {"paramsLength": len(data)})})
        return {"events": events, "cursor": events[-1]["sequence"] if events else after,
                "lost": bool(conn["events"] and after < conn["events"][0]["sequence"] - 1), "pending": pending,
                "pendingNextOffset": pending_offset + 8 if len(items) > pending_offset + 8 else None}

    async def read_event(self, owner, handle, sequence, offset=0):
        conn = self._owned(self.connections, owner, handle)
        event = next((item for item in conn["events"] if item["sequence"] == sequence), None)
        if event is None:
            raise MCPComponentError("MCP_EVENT_NOT_FOUND")
        return self._chunk(event["data"], offset)

    async def read_input(self, owner, handle, request_id, offset=0):
        conn = self._owned(self.connections, owner, handle)
        pending = conn["pending"].get(request_id)
        if pending is None:
            raise MCPComponentError("MCP_INPUT_NOT_FOUND")
        return self._chunk(pending["params"], offset)

    @staticmethod
    def _chunk(value, offset):
        if not isinstance(offset, int) or offset < 0:
            raise MCPComponentError("MCP_RESULT_RANGE_INVALID")
        data = json.dumps(value, ensure_ascii=False)
        chunk = data[offset:offset + 8192]
        return {"text": chunk, "nextOffset": offset + len(chunk), "done": offset + len(chunk) >= len(data)}

    async def respond(self, owner, handle, request_id, response):
        conn = self._owned(self.connections, owner, handle)
        pending = conn["pending"].get(request_id)
        if pending is None or pending["future"].done():
            raise MCPComponentError("MCP_INPUT_NOT_FOUND")
        if pending["type"] == "elicitation":
            types.ElicitResult.model_validate(response)
        else:
            TypeAdapter(types.CreateMessageResult | types.CreateMessageResultWithTools | types.ErrorData).validate_python(response)
        pending["future"].set_result(response)
        return {}

    async def revoke(self, owner):
        await self.revoke_scope(owner)
        await asyncio.shield(self._revocations[owner])

    async def revoke_scope(self, owner):
        """Revoke access before replying; retain the task that owns cleanup."""
        self.revoked.add(owner)
        if owner not in self._revocations:
            task = asyncio.create_task(self._revoke(owner))
            self._revocations[owner] = task
            task.add_done_callback(lambda finished: self._revoke_finished(owner, finished))
        return {}

    def _revoke_finished(self, owner, task):
        try:
            task.result()
        except Exception:
            if self.logger is not None:
                self.logger.error("MCP 插件作用域清理失败",
                    fields={"consumerPluginId": owner[0], "scopeId": owner[1], "stage": "cleanup"})
        else:
            self._revocations.pop(owner, None)

    async def _revoke(self, owner):
        conns = [conn for conn in self.connections.values() if conn["owner"] == owner]
        for conn in conns:
            await self.unregister(owner, conn["handle"])
        await asyncio.gather(*(conn["task"] for conn in conns), return_exceptions=True)
        for op in list(self.operations.values()):
            if op["owner"] == owner:
                await self.release(owner, op["operationId"])
        for conn in conns:
            self.connections.pop(conn["handle"], None)

    async def shutdown(self):
        with self._lifecycle_lock:
            pending = list(self._calls)
        await asyncio.gather(*(asyncio.wrap_future(call) for call in pending), return_exceptions=True)
        owners = set(self._revocations) | {item["owner"] for item in [*self.connections.values(), *self.operations.values()]}
        outcomes = await asyncio.gather(*(self.revoke(owner) for owner in owners), return_exceptions=True)
        failures = [error for error in outcomes if isinstance(error, Exception)]
        if failures:
            raise ExceptionGroup("MCP connection cleanup failed", failures)

    def close(self):
        with self._lifecycle_lock:
            if self._shutdown is None:
                if self.closed:
                    return
                self.closing = True
                self._shutdown = asyncio.run_coroutine_threadsafe(self.shutdown(), self.loop)
                # Stop only after the owner coroutines have returned. If the
                # deadline expires, this same cleanup continues in this loop;
                # the plugin process owner remains the bounded final fallback.
                self._shutdown.add_done_callback(lambda _: self.loop.call_soon_threadsafe(self.loop.stop))
        try:
            self._shutdown.result(timeout=CLOSE_TIMEOUT_SECONDS)
        except TimeoutError as error:
            self.failure = "MCP_CLEANUP_TIMEOUT"
            raise MCPComponentError(self.failure) from error
        except Exception:
            self.failure = "MCP_CLEANUP_FAILED"
            self.thread.join(timeout=CLOSE_TIMEOUT_SECONDS)
            raise
        self.thread.join(timeout=CLOSE_TIMEOUT_SECONDS)
        if self.thread.is_alive():
            self.failure = "MCP_CLEANUP_TIMEOUT"
            raise MCPComponentError(self.failure)
