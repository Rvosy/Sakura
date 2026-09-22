"""Controlled owner-task failures under the component's private SDK interpreter."""
import sys
from pathlib import Path

repo = Path(sys.argv[1])
sys.path[:0] = [str(repo), str(repo / "plugins/dependencies/sakura.mcp")]

import asyncio
import threading
from contextlib import contextmanager

from plugins.builtin.sakura_mcp import component as module
from plugins.builtin.sakura_mcp.component import Component, MCPComponentError


OWNER = ("fixture", "scope")


@contextmanager
def short_deadline(setting):
    previous = getattr(module, setting)
    setattr(module, setting, 0.05)
    try:
        yield
    finally:
        setattr(module, setting, previous)


def expect_error(code, call):
    try:
        call()
    except MCPComponentError as error:
        assert error.code == code, error.code
    else:
        raise AssertionError(f"expected {code}")


def blocked_cleanup_keeps_its_loop_and_single_owner():
    entered, cleanup_started = threading.Event(), threading.Event()
    release = asyncio.Event()
    cleanups = []

    class HeldConnection(Component):
        async def _connection(self, conn, credential_key, previous):
            conn["state"] = "ready"
            conn["ready"].set()
            entered.set()
            await conn["stop"].wait()
            cleanup_started.set()
            await release.wait()
            cleanups.append(conn["handle"])

    component = HeldConnection()
    original_loop = component.loop
    handle = component.call("register", OWNER, {"command": "unused"})["handle"]
    assert entered.wait(1)
    try:
        # Lifecycle access revocation is acknowledged before its owned cleanup
        # finishes. A duplicate revocation joins the same connection task.
        component.call("revoke_scope", OWNER)
        assert cleanup_started.wait(1)
        expect_error("MCP_SCOPE_CLOSED", lambda: component.call("register", OWNER, {"command": "unused"}))
        with short_deadline("CLOSE_TIMEOUT_SECONDS"):
            expect_error("MCP_CLEANUP_TIMEOUT", component.close)
        assert component.loop is original_loop and component.thread.is_alive()
        assert component.closing and not component.closed
        assert component.failure == "MCP_CLEANUP_TIMEOUT"
        expect_error("MCP_COMPONENT_CLOSING", lambda: component.call("status", OWNER))
    finally:
        component.loop.call_soon_threadsafe(release.set)
        component.close()
    assert cleanups == [handle]
    assert component.closed and not component.thread.is_alive()


def control_timeout_keeps_original_call_and_never_replays():
    entered = threading.Event()
    release = asyncio.Event()
    completed = []

    class HeldCall(Component):
        async def held(self):
            entered.set()
            await release.wait()
            completed.append("once")

    component = HeldCall()
    try:
        with short_deadline("CALL_TIMEOUT_SECONDS"):
            expect_error("MCP_COMPONENT_CALL_TIMEOUT", lambda: component.call("held"))
        assert entered.wait(1) and len(component._calls) == 1
        expect_error("MCP_COMPONENT_CALL_TIMEOUT", lambda: component.call("held"))
        with short_deadline("CLOSE_TIMEOUT_SECONDS"):
            expect_error("MCP_CLEANUP_TIMEOUT", component.close)
        assert component.thread.is_alive()
    finally:
        component.loop.call_soon_threadsafe(release.set)
        component.close()
    assert completed == ["once"]


def cancellation_becomes_terminal_only_after_the_operation_exits():
    started, cancelling = threading.Event(), threading.Event()
    release = asyncio.Event()

    class Client:
        async def call_tool(self, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelling.set()
                await release.wait()
                raise

    class HeldOperation(Component):
        async def _connection(self, conn, credential_key, previous):
            conn.update(state="ready", client=Client())
            conn["ready"].set()
            await conn["stop"].wait()
            await self._cancel_operations(conn["handle"])

        async def wait_operation(self, operation_id):
            await asyncio.gather(self.operations[operation_id]["task"], return_exceptions=True)

        async def cancel_before_start(self, handle):
            operation = await self.begin(OWNER, handle, "tools/call", {"name": "held"})
            await self.cancel(OWNER, operation["operationId"])
            return operation

    component = HeldOperation()
    handle = component.call("register", OWNER, {"command": "unused"})["handle"]
    op = component.call("begin", OWNER, handle, "tools/call", {"name": "held"})["operationId"]
    try:
        assert started.wait(1)
        component.call("cancel", OWNER, op)
        assert cancelling.wait(1)
        # Repeated cancellation cannot interrupt the task's own cleanup.
        component.call("cancel", OWNER, op)
        assert component.call("inspect", OWNER, op)["state"] == "running"
        component.loop.call_soon_threadsafe(release.set)
        component.call("wait_operation", op)
        assert component.call("inspect", OWNER, op)["state"] == "cancelled"
        component.call("release", OWNER, op)
        never_started = component.call("cancel_before_start", handle)["operationId"]
        component.call("wait_operation", never_started)
        assert component.call("inspect", OWNER, never_started)["state"] == "cancelled"
    finally:
        component.loop.call_soon_threadsafe(release.set)
        component.close()


def concurrent_release_closes_result_once():
    closed = []

    class Stream:
        def close(self):
            closed.append("closed")

    class RetainedResult(Component):
        async def release_twice(self):
            started, cancelling, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

            async def finish():
                started.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    cancelling.set()
                    await release.wait()

            async def unblock():
                await cancelling.wait()
                release.set()

            task = asyncio.create_task(finish())
            await started.wait()
            self.operations["retained"] = {"owner": OWNER, "task": task, "stream": Stream()}
            await asyncio.gather(self.release(OWNER, "retained"), self.release(OWNER, "retained"), unblock())

    component = RetainedResult()
    try:
        component.call("release_twice")
    finally:
        component.close()
    assert closed == ["closed"]


blocked_cleanup_keeps_its_loop_and_single_owner()
control_timeout_keeps_original_call_and_never_replays()
cancellation_becomes_terminal_only_after_the_operation_exits()
concurrent_release_closes_result_once()
print("owner cleanup, control timeout, and cancellation completion passed")
