from __future__ import annotations

import re

try:
    from .component import Component, MCPComponentError
except ImportError:
    from component import Component, MCPComponentError


EXPORTS = ("capabilities", "registerServer", "unregisterServer", "status", "begin", "inspect", "readResult", "cancel", "release", "events", "readEvent", "readInput", "respond")


class MCPPlugin:
    """Infrastructure service only. Consumer plugins own settings, tools and UI."""

    def setup(self, context):
        self.context = context
        self.component = Component(lambda key: context.data_path(f"oauth/{key}.json"),
                                   logger=context.get("sakura.host.logging"))
        context.effect(self.component.close)
        context.on("sakura.host.scope.closed", self.scope_closed)
        context.provide("sakura.mcp", self, exports=EXPORTS)

    def _owner(self):
        owner = self.context.caller_id
        scope = self.context.caller_scope
        if not owner or not scope:
            raise MCPComponentError("MCP_CALLER_SCOPE_REQUIRED")
        return owner, scope

    def capabilities(self):
        return {"apiVersion": 1, "sdk": "mcp 2.2.0", "transports": ["stdio", "streamable-http", "sse"],
                "operations": list(EXPORTS), "rawRequests": True, "maxRetainedOperationsPerOwner": 128,
                "resultChunkCharacters": 8192, "eventRetention": 128,
                "sampling": "opt-in callback", "elicitation": "opt-in callback", "oauth": "authorization-code",
                "subscriptions": "SDK protocol-dependent", "extensions": "raw requests and results"}

    def registerServer(self, descriptor, credential_key=None):
        owner = self._owner()
        if credential_key is not None:
            if not isinstance(credential_key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", credential_key):
                raise MCPComponentError("MCP_CREDENTIAL_KEY_INVALID")
            credential_key = owner[0] + "/" + credential_key
        return self.component.call("register", owner, descriptor, "", credential_key)

    def unregisterServer(self, handle):
        return self.component.call("unregister", self._owner(), handle)

    def status(self):
        return self.component.call("status", self._owner())

    def begin(self, handle, method, params=None, options=None):
        return self.component.call("begin", self._owner(), handle, method, params, options)

    def inspect(self, operation_id):
        return self.component.call("inspect", self._owner(), operation_id)

    def readResult(self, operation_id, offset=0, limit=8192):
        return self.component.call("read_result", self._owner(), operation_id, offset, limit)

    def cancel(self, operation_id):
        return self.component.call("cancel", self._owner(), operation_id)

    def release(self, operation_id):
        return self.component.call("release", self._owner(), operation_id)

    def events(self, handle, after=0, pending_offset=0):
        return self.component.call("events", self._owner(), handle, after, pending_offset)

    def readEvent(self, handle, sequence, offset=0):
        return self.component.call("read_event", self._owner(), handle, sequence, offset)

    def readInput(self, handle, request_id, offset=0):
        return self.component.call("read_input", self._owner(), handle, request_id, offset)

    def respond(self, handle, request_id, response):
        return self.component.call("respond", self._owner(), handle, request_id, response)

    def scope_closed(self, payload):
        # Acknowledge revocation, while the component retains the cleanup task.
        self.component.call("revoke_scope", (payload["pluginId"], payload["scopeId"]))
