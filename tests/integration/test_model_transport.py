"""通过真实本地 HTTP 连接验证模型协议、连接归属与取消。"""
from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

import pytest

from sakura_cancellation import CancellationToken, OperationCancelled
from sakura_model import ModelError
from plugins.builtin.sakura_model_openai_compatible.transport import execute


@contextmanager
def provider(respond, *, tls=None):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            request = {
                "path": self.path,
                "headers": self.headers,
                "body": json.loads(self.rfile.read(int(self.headers["Content-Length"]))),
                "connection": self.client_address,
            }
            requests.append(request)
            respond(self, request)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    if tls is not None:
        server.socket = tls.wrap_socket(server.socket, server_side=True)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    try:
        scheme = "https" if tls is not None else "http"
        yield f"{scheme}://127.0.0.1:{server.server_port}/v1", requests
    finally:
        server.shutdown()
        server.server_close()
        worker.join(3)
        assert not worker.is_alive()


def reply(handler, data=None, status=200):
    body = json.dumps(data or {"choices": [{"message": {"role": "assistant", "content": "OK"}}]}).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)
    handler.wfile.flush()


def settings(url, **overrides):
    return {"base_url": url, "api_key": "", "model": "local", "timeout_seconds": 30, **overrides}


def probe(configuration, *, cancel_checker=None, operation="test_connection"):
    request = {"messages": [{"role": "user", "content": "hello"}]}
    return execute(configuration, request, cancel_checker=cancel_checker, progress=lambda _event: None, operation=operation)


def test_local_unauthenticated_tools_images_and_compatibility_share_job_connection(monkeypatch):
    monkeypatch.setattr("urllib.request.getproxies", lambda: {"http": "http://127.0.0.1:1"})
    tool_call = {"id": "call-1", "type": "function", "function": {"name": "echo", "arguments": '{"text":"好"}'},
                 "extra_content": {"google": {"thought_signature": "opaque-signature"}}}
    calls = []
    def respond(handler, request):
        calls.append(request)
        if len(calls) == 1:
            reply(handler, {"error": {"message": "Unsupported response_format json_object"}}, 400)
        elif len(calls) == 2:
            reply(handler, {"choices": [{"message": {"role": "assistant", "content": "", "tool_calls": [tool_call]}}]})
        else:
            reply(handler)
    with provider(respond) as (url, requests):
        image = {"type": "image", "dataUrl": "data:image/png;base64,aGVsbG8="}
        messages = [{"role": "user", "content": [{"type": "text", "text": "看看"}, image]}]
        first = execute(settings(url), {"messages": messages, "responseFormat": {"type": "json_object"},
            "tools": [{"name": "echo", "parameters": {"type": "object"}}]}, cancel_checker=None, progress=lambda _event: None)
        messages.extend([first["message"], {"role": "tool", "toolCallId": "call-1", "content": "好"}])
        second = execute(settings(url), {"messages": messages}, cancel_checker=None, progress=lambda _event: None)
        assert second["message"]["content"] == "OK"
        assert len(requests) == 3
        assert requests[0]["connection"] == requests[1]["connection"]
        assert all(request["headers"].get("Authorization") is None for request in requests)
        assert requests[0]["body"]["messages"][0]["content"][1]["image_url"]["url"] == image["dataUrl"]
        assert "response_format" not in requests[1]["body"]
        assert requests[2]["body"]["messages"][-2]["tool_calls"] == [tool_call]


@pytest.mark.parametrize("operation", ["generate", "test_connection"])
@pytest.mark.parametrize("status", [429, 503])
def test_real_http_failure_is_sent_once(status, operation):
    with provider(lambda handler, _request: reply(handler, {"error": {"message": "busy"}}, status)) as (url, requests):
        with pytest.raises(ModelError) as caught:
            probe(settings(url), operation=operation)
        assert len(requests) == 1
        assert caught.value.status_code == status


@pytest.mark.parametrize("operation", ["generate", "test_connection"])
@pytest.mark.parametrize("phase", ["headers", "body"])
def test_cancellation_closes_actual_socket_before_returning(phase, operation):
    entered = threading.Event()
    disconnected = threading.Event()
    outcome = []
    token = CancellationToken()

    def respond(handler, _request):
        if phase == "body":
            handler.send_response(200)
            handler.send_header("Content-Length", "10000")
            handler.end_headers()
            handler.wfile.write(b'{"choices":')
            handler.wfile.flush()
        entered.set()
        handler.connection.settimeout(5)
        if handler.connection.recv(1) == b"":
            disconnected.set()
        handler.close_connection = True

    with provider(respond) as (url, requests):

        def run():
            try:
                probe(settings(url), operation=operation, cancel_checker=token.throw_if_cancelled)
            except BaseException as exc:
                outcome.append(exc)

        worker = threading.Thread(target=run)
        worker.start()
        try:
            assert entered.wait(5)
            token.cancel()
            worker.join(3)
            assert not worker.is_alive()
            assert len(outcome) == 1 and isinstance(outcome[0], OperationCancelled)
            assert disconnected.wait(1)
            assert len(requests) == 1
        finally:
            token.cancel()
            worker.join(5)


def test_remote_proxy_is_selected_for_each_provider_job(monkeypatch):
    monkeypatch.setattr("urllib.request.proxy_bypass", lambda _host: False)
    with provider(lambda handler, _request: reply(handler)) as (first, requests_a):
        with provider(lambda handler, _request: reply(handler)) as (second, requests_b):
            proxies = {"http": first.removesuffix("/v1")}
            monkeypatch.setattr("urllib.request.getproxies", lambda: proxies)
            config = settings("http://sakura-model.invalid/v1", api_key="key")
            assert probe(config)["message"]["content"] == "OK"
            proxies["http"] = second.removesuffix("/v1")
            assert probe(config)["message"]["content"] == "OK"
            assert len(requests_a) == len(requests_b) == 1
            assert all(request["path"] == "http://sakura-model.invalid/v1/chat/completions" for request in requests_a + requests_b)


@pytest.mark.parametrize("operation", ["generate", "test_connection"])
def test_custom_certificate_trust_works_for_local_https(monkeypatch, tmp_path, operation):
    from datetime import datetime, timedelta, timezone
    import ipaddress
    import ssl
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(x509.oid.NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path = tmp_path / "server.pem"
    key_path = tmp_path / "server.key"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    monkeypatch.setenv("SSL_CERT_FILE", str(cert_path))
    with provider(lambda handler, _request: reply(handler), tls=context) as (url, requests):
        assert probe(settings(url), operation=operation)["message"]["content"] == "OK"
        assert len(requests) == 1


@pytest.mark.parametrize("operation", ["generate", "test_connection"])
def test_redirect_is_reported_without_replaying_post(operation):
    def respond(handler, _request):
        handler.send_response(307)
        handler.send_header("Location", "/v1/other/chat/completions")
        handler.send_header("Content-Length", "0")
        handler.end_headers()

    with provider(respond) as (url, requests):
        with pytest.raises(ModelError) as caught:
            probe(settings(url), operation=operation)
        assert caught.value.status_code == 307
        assert len(requests) == 1
