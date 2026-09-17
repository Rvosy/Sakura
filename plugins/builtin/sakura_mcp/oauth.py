"""Loopback handoff and private storage; OAuth protocol work belongs to the SDK."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import os
from urllib.parse import parse_qs, urlsplit

from mcp.client.auth import OAuthClientProvider
from mcp.shared.auth import AuthorizationCodeResult, OAuthClientInformationFull, OAuthClientMetadata, OAuthToken


class Storage:
    def __init__(self, path):
        self.path = path
        self.data = json.loads(path.read_text(encoding="utf-8")) if path and path.is_file() else {}

    def save(self):
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            if os.name != "nt":
                os.chmod(temporary, 0o600)
            json.dump(self.data, stream)
        os.replace(temporary, self.path)

    async def get_tokens(self):
        value = self.data.get("tokens")
        return OAuthToken.model_validate(value) if value else None

    async def set_tokens(self, tokens):
        self.data["tokens"] = tokens.model_dump(mode="json")
        self.save()

    async def get_client_info(self):
        value = self.data.get("client")
        return OAuthClientInformationFull.model_validate(value) if value else None

    async def set_client_info(self, client_info):
        self.data["client"] = client_info.model_dump(mode="json")
        self.save()


@asynccontextmanager
async def authorization(config, path, announce):
    storage = Storage(path)
    options = config["oauth"]
    if options is True:
        options = {}
    # Credentials are bound to the configured endpoint, not merely the UI row ID.
    if storage.data.get("url") != config["url"]:
        storage.data = {"url": config["url"]}
    queue = asyncio.Queue(maxsize=1)

    async def callback(reader, writer):
        try:
            async with asyncio.timeout(5):
                line = (await reader.readline()).decode("ascii")
                method, target, _ = line.split(" ", 2)
                parsed = urlsplit(target)
                query = parse_qs(parsed.query)
                if method != "GET" or parsed.path != "/callback" or not query.get("code") or not query.get("state"):
                    writer.write(b"HTTP/1.1 400 Bad Request\r\nConnection: close\r\nContent-Length: 0\r\n\r\n")
                else:
                    result = AuthorizationCodeResult(code=query["code"][0], state=query["state"][0], iss=query.get("iss", [None])[0])
                    if queue.empty():
                        queue.put_nowait(result)
                    body = b"Return to Sakura to check authorization."
                    writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nConnection: close\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
                await writer.drain()
        except (ValueError, UnicodeError, OSError, TimeoutError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(callback, "127.0.0.1", storage.data.get("port", options.get("port", 0)), limit=8192)
    async with server:
        port = server.sockets[0].getsockname()[1]
        storage.data["port"] = port
        storage.save()
        metadata = {"client_name": "Sakura", **options.get("clientMetadata", {}),
                    "redirect_uris": [f"http://127.0.0.1:{port}/callback"]}
        if options.get("clientInfo") and not await storage.get_client_info():
            await storage.set_client_info(OAuthClientInformationFull.model_validate(options["clientInfo"]))

        async def redirect(url):
            announce(url)

        async def wait_callback():
            result = await queue.get()
            announce(None)
            return result

        yield OAuthClientProvider(server_url=config["url"], client_metadata=OAuthClientMetadata.model_validate(metadata),
            storage=storage, redirect_handler=redirect, callback_handler=wait_callback,
            client_metadata_url=options.get("clientMetadataUrl"))
