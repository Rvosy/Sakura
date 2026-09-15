"""Runs outside the Core interpreter so the SDK stays a private dependency."""
import sys
from pathlib import Path

repo = Path(sys.argv[1])
dependency = repo / "plugins/dependencies/sakura.mcp"
sys.path[:0] = [str(repo), str(dependency), str(dependency / "win32"), str(dependency / "win32/lib"), str(dependency / "pywin32_system32")]

import asyncio
import importlib.util
import json
import socket
import threading
import time
import uvicorn
import httpx2
from urllib.parse import parse_qs, urlencode
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse
from plugins.builtin.sakura_mcp.component import Component, MCPComponentError

spec = importlib.util.spec_from_file_location("fixture_server", repo / "tests/fixtures/mcp_component/server.py")
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


def until(check):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        threading.Event().wait(0.01)
    raise AssertionError("condition not reached")


def exercise(config):
    config["elicitation"] = True
    component = Component()
    owner = ("test", "scope-one")
    try:
        handle = component.call("register", owner, config)["handle"]
        until(lambda: component.call("status", owner)[0]["state"] != "connecting")
        state = component.call("status", owner)[0]
        assert state["state"] == "ready", state

        def request(method, params=None):
            op = component.call("begin", owner, handle, method, params)["operationId"]
            result = until(lambda: (value if (value := component.call("inspect", owner, op))["state"] != "running" else None))
            assert result["state"] == "completed", result
            return op, result

        _, tools = request("tools/list")
        assert {item["name"] for item in tools["result"]["tools"]} == {"echo", "hold", "pid", "question"}
        _, result = request("tools/call", {"name": "echo", "arguments": {"text": "你好"}})
        assert result["result"]["content"][0]["text"] == "你好"
        _, result = request("resources/read", {"uri": "test://hello"})
        assert result["result"]["contents"][0]["text"] == "资源内容"
        _, result = request("prompts/get", {"name": "welcome", "arguments": {"name": "Sakura"}})
        assert "Sakura" in result["result"]["messages"][0]["content"]["text"]
        op = component.call("begin", owner, handle, "tools/call", {"name": "question"})["operationId"]
        pending = until(lambda: component.call("events", owner, handle)["pending"])[0]
        assert pending["type"] == "elicitation", pending
        component.call("respond", owner, handle, pending["requestId"], {"action": "accept", "content": {"name": "callback answer"}})
        answer = until(lambda: (value if (value := component.call("inspect", owner, op))["state"] != "running" else None))
        assert answer["state"] == "completed", answer
        assert answer["result"]["content"][0]["text"] == "callback answer"
        large = "汉字🌸" * 12000
        op, result = request("tools/call", {"name": "echo", "arguments": {"text": large}})
        assert result["result"] is None
        chunks, offset = [], 0
        while True:
            piece = component.call("read_result", owner, op, offset)
            chunks.append(piece["text"])
            offset = piece["nextOffset"]
            if piece["done"]:
                break
        assert json.loads("".join(chunks))["content"][0]["text"] == large
        component.call("release", owner, op)
        op = component.call("begin", owner, handle, "tools/call", {"name": "hold"})["operationId"]
        until(lambda: component.call("inspect", owner, op).get("progress"))
        component.call("cancel", owner, op)
        until(lambda: component.call("inspect", owner, op)["state"] == "cancelled")
        request("tools/call", {"name": "echo", "arguments": {"text": "after cancellation"}})
        try:
            component.call("begin", ("test", "scope-two"), handle, "tools/list")
        except MCPComponentError as error:
            assert error.code == "MCP_HANDLE_NOT_FOUND"
        else:
            raise AssertionError("foreign scope accessed handle")
        component.call("revoke", owner)
        assert component.call("status", owner) == []
        assert not component.operations
    finally:
        component.close()
    assert not component.thread.is_alive()


exercise({"command": sys.executable, "args": ["-I", "-S", str(repo / "tests/fixtures/mcp_component/server.py"), str(dependency)]})
exercise({"command": sys.executable, "args": ["-I", "-S", str(repo / "tests/fixtures/mcp_component/server.py"), str(dependency)], "mode": "legacy"})
for transport in ("streamable-http", "sse"):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    app = fixture.server.streamable_http_app() if transport == "streamable-http" else fixture.server.sse_app()
    host = uvicorn.Server(uvicorn.Config(app, log_level="critical", lifespan="on"))
    thread = threading.Thread(target=lambda: host.run(sockets=[sock]), daemon=True)
    thread.start()
    try:
        until(lambda: host.started)
        exercise({"transport": transport, "url": f"http://127.0.0.1:{port}/" + ("mcp" if transport == "streamable-http" else "sse"), "mode": "legacy" if transport == "sse" else "auto"})
    finally:
        host.should_exit = True
        thread.join(timeout=10)
        sock.close()
    assert not thread.is_alive()
print("stdio modern/legacy, HTTP, SSE, resources, prompts, cancellation, large results and ownership passed")


def exercise_oauth():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    base = f"http://127.0.0.1:{sock.getsockname()[1]}"
    app = fixture.server.streamable_http_app()
    registrations = []

    async def protected(scope, receive, send):
        if scope["type"] != "http":
            return await app(scope, receive, send)
        request = Request(scope, receive)
        path = scope["path"]
        if path.startswith("/.well-known/oauth-protected-resource"):
            response = JSONResponse({"resource": base + "/mcp", "authorization_servers": [base]})
        elif path.startswith("/.well-known/"):
            response = JSONResponse({"issuer": base, "authorization_endpoint": base + "/authorize", "token_endpoint": base + "/token", "registration_endpoint": base + "/register", "response_types_supported": ["code"], "grant_types_supported": ["authorization_code", "refresh_token"], "code_challenge_methods_supported": ["S256"], "token_endpoint_auth_methods_supported": ["none"]})
        elif path == "/register":
            metadata = await request.json()
            registrations.append(metadata)
            response = JSONResponse({**metadata, "client_id": "fixture-client"}, status_code=201)
        elif path == "/authorize":
            params = request.query_params
            assert params["code_challenge_method"] == "S256"
            response = RedirectResponse(params["redirect_uri"] + "?" + urlencode({"code": "fixture-code", "state": params["state"], "iss": base}))
        elif path == "/token":
            form = parse_qs((await request.body()).decode())
            assert form["code"] == ["fixture-code"]
            assert form.get("code_verifier")
            response = JSONResponse({"access_token": "fixture-token", "token_type": "Bearer", "expires_in": 3600})
        elif request.headers.get("authorization") != "Bearer fixture-token":
            response = JSONResponse({}, status_code=401, headers={"WWW-Authenticate": f'Bearer resource_metadata="{base}/.well-known/oauth-protected-resource"'})
        else:
            return await app(scope, receive, send)
        await response(scope, receive, send)

    host = uvicorn.Server(uvicorn.Config(protected, log_level="critical", lifespan="on"))
    thread = threading.Thread(target=lambda: host.run(sockets=[sock]), daemon=True)
    thread.start()
    auth_root = Path(sys.argv[2])
    owner = ("oauth", "scope")
    try:
        until(lambda: host.started)
        for attempt in range(2):
            component = Component(lambda key: auth_root / (key + ".json"))
            try:
                component.call("register", owner, {"url": base + "/mcp", "oauth": True}, "fixture", "server")
                if attempt == 0:
                    url = until(lambda: component.call("authorizations", owner))[0]
                    with httpx2.Client(follow_redirects=True, trust_env=False) as browser:
                        response = browser.get(url)
                    assert response.status_code == 200
                state = until(lambda: (value if (value := component.call("status", owner)[0])["state"] != "connecting" else None))
                assert state["state"] == "ready", state
            finally:
                component.close()
        assert len(registrations) == 1, "restart must reuse registered client and tokens"
    finally:
        host.should_exit = True
        thread.join(timeout=10)
        sock.close()


exercise_oauth()
print("OAuth discovery, registration, PKCE, loopback handoff and persisted credentials passed")
