from __future__ import annotations

import json
import ipaddress
import secrets
import socket
import threading
import time
from collections import deque
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from app.core.mobile_chat_bridge import MobileChatBusyError
from app.core.debug_log import debug_log


DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8765
MAX_REQUEST_BYTES = 12 * 1024 * 1024
SOCKET_TIMEOUT_SECONDS = 30
MAX_CONCURRENT_REQUESTS = 8
MAX_REQUESTS_PER_MINUTE = 60
TAILSCALE_CGNAT = ipaddress.ip_network("100.64.0.0/10")


class SakuraMobileHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = 64

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._request_slots = threading.BoundedSemaphore(MAX_CONCURRENT_REQUESTS)
        self._rate_lock = threading.Lock()
        self._request_times: dict[str, deque[float]] = {}
        super().__init__(*args, **kwargs)

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self._request_slots.acquire(blocking=False):
            request.close()
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._request_slots.release()

    def allow_client_request(self, client_address: object) -> bool:
        client = _client_address_text(client_address).rsplit(":", 1)[0]
        now = time.monotonic()
        with self._rate_lock:
            requests = self._request_times.setdefault(client, deque())
            while requests and now - requests[0] >= 60:
                requests.popleft()
            if len(requests) >= MAX_REQUESTS_PER_MINUTE:
                return False
            requests.append(now)
            return True

    def get_request(self):  # type: ignore[no-untyped-def]
        request, client_address = super().get_request()
        request.settimeout(SOCKET_TIMEOUT_SECONDS)
        service = getattr(self, "service", None)
        _write_mobile_access_log(
            getattr(service, "base_dir", None),
            "tcp_connection_accepted",
            {"client": _client_address_text(client_address)},
        )
        debug_log(
            "Mobile",
            "TCP connection accepted",
            {"client": _client_address_text(client_address)},
        )
        return request, client_address

    def handle_error(self, request: object, client_address: object) -> None:
        service = getattr(self, "service", None)
        _write_mobile_access_log(
            getattr(service, "base_dir", None),
            "http_connection_handler_failed",
            {"client": _client_address_text(client_address)},
        )
        debug_log(
            "Mobile",
            "HTTP connection handler failed",
            {"client": _client_address_text(client_address)},
        )
        super().handle_error(request, client_address)


class MobilePluginService:
    """HTTP 层到宿主插件服务门面的轻量适配器。"""

    def __init__(self, base_dir: Path, mobile_service: Any) -> None:
        self.base_dir = base_dir
        self.mobile_service = mobile_service

    def characters(self) -> list[dict[str, str]]:
        return self.mobile_service.characters()

    def history(self, character_id: str, *, limit: int = 50) -> list[dict[str, str]]:
        return self.mobile_service.history(character_id, limit=limit)

    def chat(self, character_id: str, text: str, image_data_url: str = "") -> dict[str, Any]:
        return self.mobile_service.chat(character_id, text, image_data_url)


def run_mobile_server(
    base_dir: Path,
    mobile_service: Any,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    token: str = "",
) -> ThreadingHTTPServer:
    service = MobilePluginService(base_dir, mobile_service)
    clean_token = token.strip() or secrets.token_urlsafe(10)
    handler_class = _build_handler(service, clean_token)
    server = SakuraMobileHTTPServer((host, port), handler_class)
    server.service = service  # type: ignore[attr-defined]
    server.mobile_token = clean_token  # type: ignore[attr-defined]
    _write_mobile_access_log(
        base_dir,
        "server_created",
        {"host": host, "port": port, "plugin_mode": True},
    )
    return server


def mobile_access_urls(host: str, port: int, token: str) -> dict[str, Any]:
    query = f"?token={token.strip()}"
    local_url = f"http://127.0.0.1:{int(port)}/{query}"
    lan_urls = [
        f"http://{address}:{int(port)}/{query}"
        for address in local_ipv4_addresses()
    ]
    return {
        "host": host,
        "local_url": local_url,
        "lan_urls": lan_urls,
    }


def local_ipv4_addresses() -> list[str]:
    addresses: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addresses.add(str(info[4][0]))
    except OSError:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            addresses.add(str(sock.getsockname()[0]))
    except OSError:
        pass
    return sorted(address for address in addresses if _is_lan_ipv4(address))


def _is_lan_ipv4(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if ip.version != 4 or ip.is_loopback or ip.is_unspecified or ip.is_multicast:
        return False
    return ip.is_private or ip in TAILSCALE_CGNAT


def _build_handler(service: MobilePluginService, token: str) -> type[BaseHTTPRequestHandler]:
    class MobileRequestHandler(BaseHTTPRequestHandler):
        server_version = "SakuraMobile/0.1"

        def do_GET(self) -> None:  # noqa: N802
            try:
                parsed = urlparse(self.path)
                self._require_rate_limit()
                self._log_request_start("GET", parsed.path)
                if parsed.path in {"", "/"}:
                    self._require_token(parsed)
                    self._send_html(_mobile_html(token))
                    return
                if parsed.path == "/api/status":
                    self._require_token(parsed)
                    self._send_json({"ok": True})
                    return
                if parsed.path == "/api/characters":
                    self._require_token(parsed)
                    self._send_json({"characters": service.characters()})
                    return
                if parsed.path == "/api/history":
                    self._require_token(parsed)
                    params = parse_qs(parsed.query)
                    character_id = _first_query_value(params, "character_id")
                    limit = _safe_int(_first_query_value(params, "limit"), 50)
                    self._send_json({"history": service.history(character_id, limit=limit)})
                    return
                self._send_error(HTTPStatus.NOT_FOUND, "Not found")
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                self._log_client_disconnected()
            except Exception as exc:  # noqa: BLE001
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))

        def do_POST(self) -> None:  # noqa: N802
            try:
                parsed = urlparse(self.path)
                self._require_rate_limit()
                self._log_request_start("POST", parsed.path)
                if parsed.path != "/api/chat":
                    self._send_error(HTTPStatus.NOT_FOUND, "Not found")
                    return
                data = self._read_json_body()
                self._require_token(parsed, data)
                result = service.chat(
                    str(data.get("character_id") or ""),
                    str(data.get("text") or ""),
                    str(data.get("image") or data.get("image_data_url") or ""),
                )
                self._send_json(result)
            except MobileChatBusyError as exc:
                self._send_json(
                    {"ok": False, "busy": True, "error": str(exc)},
                    HTTPStatus.CONFLICT,
                )
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                self._log_client_disconnected()
            except Exception as exc:  # noqa: BLE001
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))

        def do_OPTIONS(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            self._log_request_start("OPTIONS", parsed.path)
            self.send_response(HTTPStatus.NO_CONTENT.value)
            self._send_common_headers()
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            debug_log("Mobile", "HTTP 请求", {"message": format % args})

        def _log_request_start(self, method: str, path: str) -> None:
            request_info = {
                "method": method,
                "path": path,
                "client": _client_address_text(self.client_address),
                "host": self.headers.get("Host", ""),
                "origin": self.headers.get("Origin", ""),
                "user_agent": self.headers.get("User-Agent", ""),
            }
            _write_mobile_access_log(service.base_dir, "http_request_received", request_info)
            debug_log(
                "Mobile",
                "HTTP request received",
                request_info,
            )

        def _log_client_disconnected(self) -> None:
            debug_log("Mobile", "HTTP client disconnected", {"client": _client_address_text(self.client_address)})

        def _read_json_body(self) -> dict[str, Any]:
            length = _safe_int(self.headers.get("Content-Length"), 0)
            if length <= 0:
                return {}
            if length > MAX_REQUEST_BYTES:
                raise ValueError("请求体过大。")
            raw = self.rfile.read(length).decode("utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("请求体必须是 JSON object。")
            return data

        def _require_token(self, parsed: Any, data: dict[str, Any] | None = None) -> None:
            params = parse_qs(parsed.query)
            provided = (
                self.headers.get("X-Sakura-Mobile-Token", "")
                or _first_query_value(params, "token")
                or str((data or {}).get("token") or "")
            ).strip()
            if provided != token:
                raise ValueError("配对码无效。")

        def _require_rate_limit(self) -> None:
            if not self.server.allow_client_request(self.client_address):  # type: ignore[attr-defined]
                raise ValueError("请求过于频繁，请稍后再试。")

        def _send_json(self, data: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
            payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status.value)
            self._send_common_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _send_html(self, html: str) -> None:
            payload = html.encode("utf-8")
            self.send_response(HTTPStatus.OK.value)
            self._send_common_headers()
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _send_common_headers(self) -> None:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Sakura-Mobile-Token")
            self.send_header("Connection", "close")

        def _send_error(self, status: HTTPStatus, message: str) -> None:
            self._send_json({"ok": False, "error": message}, status)

    return MobileRequestHandler


def _first_query_value(params: dict[str, list[str]], key: str) -> str:
    values = params.get(key) or []
    return values[0] if values else ""


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _client_address_text(client_address: object) -> str:
    if isinstance(client_address, tuple) and len(client_address) >= 2:
        return f"{client_address[0]}:{client_address[1]}"
    return str(client_address)


def _write_mobile_access_log(base_dir: Path | None, event: str, data: dict[str, Any]) -> None:
    root = base_dir if base_dir is not None else Path(__file__).resolve().parents[2]
    path = root / "data" / "logs" / "mobile-server.log"
    record = {
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        "event": event,
        "data": data,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except OSError:
        pass


def _mobile_html(token: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Sakura Mobile</title>
  <style>
    :root {{ color-scheme: light; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    body {{ margin: 0; background: #f7f4fb; color: #172033; overflow: hidden; }}
    main {{ height: 100vh; height: 100dvh; display: grid; grid-template-rows: auto minmax(0, 1fr) auto; }}
    header {{ display: flex; gap: 10px; align-items: center; padding: 12px; background: #b9c4ff; position: sticky; top: 0; }}
    h1 {{ font-size: 18px; margin: 0; flex: 1; }}
    select, button, textarea {{ font: inherit; }}
    select, textarea {{ border: 1px solid #9facdf; border-radius: 8px; background: white; }}
    select {{ padding: 8px; max-width: 48vw; }}
    #chat {{ min-height: 0; padding: 14px; overflow-y: auto; display: flex; flex-direction: column; gap: 10px; }}
    .msg {{ max-width: 82%; padding: 10px 12px; border-radius: 14px; line-height: 1.45; white-space: pre-wrap; word-break: break-word; box-shadow: 0 1px 2px #0001; }}
    .user {{ align-self: flex-end; background: #c8eef4; }}
    .assistant {{ align-self: flex-start; background: white; }}
    .typing .body {{ display: inline-flex; gap: 4px; align-items: center; min-width: 32px; min-height: 18px; }}
    .typing-dot {{ width: 6px; height: 6px; border-radius: 50%; background: #7b8097; opacity: .35; animation: typingPulse 1s infinite ease-in-out; }}
    .typing-dot:nth-child(2) {{ animation-delay: .16s; }}
    .typing-dot:nth-child(3) {{ animation-delay: .32s; }}
    @keyframes typingPulse {{ 0%, 80%, 100% {{ transform: translateY(0); opacity: .35; }} 40% {{ transform: translateY(-3px); opacity: .95; }} }}
    .meta {{ font-size: 12px; color: #667; margin-bottom: 4px; }}
    form {{ display: grid; grid-template-columns: auto 1fr auto; gap: 8px; align-items: end; padding: 12px; background: #e8e6ff; }}
    .media-actions {{ display: grid; gap: 6px; }}
    .media-button {{ display: inline-flex; align-items: center; justify-content: center; min-width: 54px; min-height: 34px; padding: 0 8px; border-radius: 8px; background: #fff; border: 1px solid #9facdf; color: #172033; font-size: 13px; }}
    .media-button:active {{ background: #eef2ff; }}
    .media-button.selected {{ border-color: #0095b6; box-shadow: 0 0 0 2px #0095b622; }}
    .file-input {{ position: absolute; width: 1px; height: 1px; opacity: 0; pointer-events: none; }}
    textarea {{ min-height: 42px; max-height: 140px; padding: 10px; resize: vertical; }}
    button {{ border: 0; border-radius: 8px; background: #0095b6; color: white; padding: 10px 14px; }}
    button:disabled {{ opacity: .55; }}
    #status {{ font-size: 12px; color: #5b6078; padding: 0 12px 8px; background: #e8e6ff; }}
  </style>
</head>
<body>
<main>
  <header>
    <h1>Sakura</h1>
    <select id="character"></select>
  </header>
  <section id="chat"></section>
  <div>
    <form id="form">
      <div class="media-actions">
        <label id="albumButton" class="media-button" for="image">相册</label>
        <label id="cameraButton" class="media-button" for="camera">拍照</label>
      </div>
      <input id="image" class="file-input" type="file" accept="image/*">
      <input id="camera" class="file-input" type="file" accept="image/*" capture="environment">
      <textarea id="text" placeholder="发消息给她..."></textarea>
      <button id="send" type="submit">发送</button>
    </form>
    <div id="status"></div>
  </div>
</main>
<script>
const TOKEN = {json.dumps(token)};
const chat = document.querySelector('#chat');
const character = document.querySelector('#character');
const form = document.querySelector('#form');
const text = document.querySelector('#text');
const image = document.querySelector('#image');
const camera = document.querySelector('#camera');
const albumButton = document.querySelector('#albumButton');
const cameraButton = document.querySelector('#cameraButton');
const send = document.querySelector('#send');
const statusLine = document.querySelector('#status');
let replyQueueToken = 0;
let selectedImageSource = '';

function api(path) {{ return path + (path.includes('?') ? '&' : '?') + 'token=' + encodeURIComponent(TOKEN); }}
function setStatus(value) {{ statusLine.textContent = value || ''; }}
function sleep(ms) {{ return new Promise(resolve => setTimeout(resolve, ms)); }}
async function fetchWithTimeout(url, options = {{}}) {{
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 60000);
  try {{
    return await fetch(url, {{ ...options, signal: controller.signal }});
  }} finally {{
    clearTimeout(timer);
  }}
}}
async function fetchJson(path, options = {{}}) {{
  const res = await fetchWithTimeout(api(path), options);
  let data = {{}};
  try {{
    data = await res.json();
  }} catch (_err) {{}}
  if (!res.ok) throw new Error(data.error || '请求失败');
  return data;
}}
function errorText(err) {{
  if (err && err.name === 'AbortError') return '请求超时，请稍后再试。';
  return String(err && err.message ? err.message : err);
}}
function cleanAssistantText(value) {{
  return String(value || '').trim().replace(/^[.．…]+\\s*/, '').trimStart();
}}
function scrollChatToBottom() {{
  requestAnimationFrame(() => requestAnimationFrame(() => {{
    chat.scrollTop = chat.scrollHeight;
    const page = document.scrollingElement || document.documentElement;
    if (page) page.scrollTop = page.scrollHeight;
  }}));
}}
function addMessage(role, content, options = {{}}) {{
  const node = document.createElement('div');
  node.className = 'msg ' + role;
  if (options.typing) node.className += ' typing';
  const meta = document.createElement('div');
  meta.className = 'meta';
  meta.textContent = role === 'user' ? '你' : 'Sakura';
  const body = document.createElement('div');
  body.className = 'body';
  body.textContent = content;
  node.append(meta, body);
  chat.append(node);
  scrollChatToBottom();
  return node;
}}
function addTypingMessage() {{
  const node = addMessage('assistant', '', {{ typing: true }});
  const body = node.querySelector('.body');
  body.textContent = '';
  for (let index = 0; index < 3; index += 1) {{
    const dot = document.createElement('span');
    dot.className = 'typing-dot';
    body.append(dot);
  }}
  return node;
}}
function selectedImageFile() {{
  if (selectedImageSource === 'camera') return camera.files && camera.files[0];
  if (selectedImageSource === 'album') return image.files && image.files[0];
  return (camera.files && camera.files[0]) || (image.files && image.files[0]);
}}
function syncMediaSelection(source) {{
  selectedImageSource = source;
  albumButton.classList.toggle('selected', source === 'album' && image.files && image.files.length > 0);
  cameraButton.classList.toggle('selected', source === 'camera' && camera.files && camera.files.length > 0);
  if (source === 'album' && image.files && image.files.length > 0) camera.value = '';
  if (source === 'camera' && camera.files && camera.files.length > 0) image.value = '';
}}
async function showAssistantSegments(segments) {{
  const token = ++replyQueueToken;
  const items = segments
    .map(segment => cleanAssistantText(segment.content))
    .filter(Boolean);
  for (let index = 0; index < items.length; index += 1) {{
    const typing = addTypingMessage();
    await sleep(index === 0 ? 800 : 950);
    if (token !== replyQueueToken) {{
      typing.remove();
      return;
    }}
    typing.remove();
    addMessage('assistant', items[index]);
    await sleep(Math.min(1800, 700 + items[index].length * 22));
  }}
}}
async function loadCharacters() {{
  const data = await fetchJson('/api/characters');
  character.innerHTML = '';
  for (const item of data.characters || []) {{
    const opt = document.createElement('option');
    opt.value = item.id;
    opt.textContent = item.name;
    if (item.current === 'true') opt.selected = true;
    character.append(opt);
  }}
  await loadHistory();
}}
async function loadHistory() {{
  if (!character.value) return;
  replyQueueToken += 1;
  chat.innerHTML = '';
  const data = await fetchJson('/api/history?character_id=' + encodeURIComponent(character.value) + '&limit=50');
  for (const item of data.history || []) {{
    if (item.role === 'user' || item.role === 'assistant') addMessage(item.role, item.content);
  }}
  scrollChatToBottom();
}}
function readImage(file) {{
  return new Promise((resolve, reject) => {{
    if (!file) return resolve('');
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ''));
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  }});
}}
character.addEventListener('change', loadHistory);
image.addEventListener('change', () => syncMediaSelection('album'));
camera.addEventListener('change', () => syncMediaSelection('camera'));
form.addEventListener('submit', async (event) => {{
  event.preventDefault();
  const value = text.value.trim();
  const file = selectedImageFile();
  if (!value && !file) return;
  send.disabled = true;
  setStatus('她正在思考...');
  addMessage('user', value + (file ? '\\n（已附加图片）' : ''));
  try {{
    const imageData = await readImage(file);
    text.value = '';
    image.value = '';
    camera.value = '';
    syncMediaSelection('');
    const data = await fetchJson('/api/chat', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{
        token: TOKEN,
        character_id: character.value,
        text: value,
        image: imageData
      }})
    }});
    const segments = Array.isArray(data.segments) ? data.segments : [];
    if (segments.length) {{
      await showAssistantSegments(segments);
    }} else {{
      await showAssistantSegments([{{ content: data.reply || '' }}]);
    }}
    setStatus('');
  }} catch (err) {{
    setStatus(errorText(err));
  }} finally {{
    send.disabled = false;
  }}
}});
loadCharacters().catch(err => setStatus(errorText(err)));
</script>
</body>
</html>"""
