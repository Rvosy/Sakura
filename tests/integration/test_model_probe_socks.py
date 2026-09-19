"""The provider settings probe uses the same configured SOCKS proxy as model calls."""
from contextlib import contextmanager
from http.client import parse_headers
import json
import socketserver
import threading

import pytest

from plugins.builtin.sakura_model_openai_compatible.transport import execute


@contextmanager
def socks_proxy():
    destinations, requests, errors = [], [], []

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            try:
                self.connection.settimeout(5)
                version, count = self.rfile.read(2)
                assert version == 5 and 0 in self.rfile.read(count)
                self.wfile.write(b"\x05\x00")
                version, command, reserved, kind = self.rfile.read(4)
                assert (version, command, reserved, kind) == (5, 1, 0, 3)
                length = self.rfile.read(1)[0]
                host = self.rfile.read(length).decode("ascii")
                port = int.from_bytes(self.rfile.read(2), "big")
                destinations.append((host, port))
                # Accept the tunnel locally; no DNS or upstream connection occurs.
                self.wfile.write(b"\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x50")
                method, path, _ = self.rfile.readline().decode("ascii").split()
                headers = parse_headers(self.rfile)
                body = json.loads(self.rfile.read(int(headers["Content-Length"])))
                requests.append((method, path, body))
                payload = json.dumps({"choices": [{"message": {"role": "assistant", "content": "OK"}}]}).encode()
                self.wfile.write(
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\nContent-Length: "
                    + str(len(payload)).encode() + b"\r\n\r\n" + payload
                )
            except BaseException as error:
                errors.append(error)

    with socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler) as server:
        worker = threading.Thread(target=server.serve_forever)
        worker.start()
        try:
            yield server.server_address[1], destinations, requests
        finally:
            server.shutdown()
            worker.join(5)
            assert not worker.is_alive()
    assert not errors


@pytest.mark.parametrize("scheme", ["socks5", "socks5h"])
def test_provider_model_probe_uses_configured_socks_proxy(monkeypatch, scheme):
    monkeypatch.setattr("urllib.request.proxy_bypass", lambda _host: False)
    with socks_proxy() as (port, destinations, requests):
        monkeypatch.setattr("urllib.request.getproxies", lambda: {"all": f"{scheme}://127.0.0.1:{port}"})
        result = execute({"base_url": "http://sakura-proxy-fixture.invalid/v1", "api_key": "fixture-key", "model": "fixture-model", "timeout_seconds": 5}, {}, cancel_checker=None, progress=lambda _event: None, operation="test_connection")
        assert result["message"]["content"] == "OK"
    assert destinations == [("sakura-proxy-fixture.invalid", 80)]
    assert len(requests) == 1
    assert requests[0][:2] == ("POST", "/v1/chat/completions")
    assert requests[0][2]["model"] == "fixture-model"
