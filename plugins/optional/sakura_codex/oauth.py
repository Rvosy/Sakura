"""ChatGPT 设备码登录与本插件的登录凭据。"""
from __future__ import annotations

import asyncio
import base64
from copy import deepcopy
import json
import os
from pathlib import Path
import threading
import time

from sakura_cancellation import OperationCancelled, await_cancellable, check_cancelled
from sakura_model import ModelError
from sakura_provider_errors import public_provider_http_message


CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
ISSUER = "https://auth.openai.com"
DEVICE_URL = ISSUER + "/codex/device"


def http_client(url, timeout):
    import httpx
    from sakura_http import proxy_for_url
    return httpx.AsyncClient(proxy=proxy_for_url(url), trust_env=False, timeout=timeout,
                             follow_redirects=False, headers={"User-Agent": "Sakura/codex-subscription"})


def checked_json(response, *, secrets=()):
    data = response.json() if response.is_success else None
    if not response.is_success or data.get("error") is not None:
        status = response.status_code
        failure = ModelError("MODEL_AUTHENTICATION_FAILED" if status in {401, 403} else "MODEL_RATE_LIMITED" if status == 429 else "MODEL_HTTP_FAILED",
            public_provider_http_message(RuntimeError(f"API HTTP {status}: {response.text}"), status, secrets=secrets),
            diagnostics={"httpStatus": status})
        failure.diagnostic_secrets = tuple(secrets)
        raise failure
    return data


def _claims(token):
    if not token:
        return {}
    try:
        payload = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (ValueError, IndexError, UnicodeDecodeError):
        return {}


def credentials(tokens, previous=None):
    value = {**(previous or {}), **tokens}
    access = value.get("access_token")
    if not access:
        raise ModelError("CODEX_LOGIN_INVALID", "登录响应缺少访问令牌。")
    claims = _claims(access)
    identity = _claims(value.get("id_token"))
    account = {**claims.get("https://api.openai.com/auth", {}), **identity.get("https://api.openai.com/auth", {})}
    account_id = account.get("chatgpt_account_id") or value.get("account_id")
    if not account_id:
        raise ModelError("CODEX_LOGIN_INVALID", "登录响应缺少 ChatGPT 账号信息。")
    profile = claims.get("https://api.openai.com/profile", {})
    value.update(account_id=account_id, email=identity.get("email") or profile.get("email") or value.get("email", ""),
                 plan=account.get("chatgpt_plan_type") or value.get("plan", ""),
                 expires_at=time.time() + tokens["expires_in"] if tokens.get("expires_in") else claims.get("exp", 0))
    return value


class Account:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.refresh_lock = threading.Lock()
        self.generation = 0
        self.closed = False
        self.value = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else None

    def snapshot(self):
        with self.lock:
            if self.closed:
                raise OperationCancelled()
            if not self.value:
                raise ModelError("CODEX_LOGIN_REQUIRED", "请先登录 ChatGPT。")
            return {"generation": self.generation, "tokens": deepcopy(self.value)}

    def public(self):
        with self.lock:
            value = self.value or {}
            return {"loggedIn": bool(value), "email": value.get("email", ""), "plan": value.get("plan", "")}

    def _save(self, value):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(value, file, ensure_ascii=False)
        temporary.replace(self.path)
        self.value = deepcopy(value)

    def logout(self):
        with self.lock:
            self.path.unlink(missing_ok=True)
            self.value = None
            self.generation += 1

    def close(self):
        with self.lock:
            self.closed = True
            self.generation += 1

    def login(self, cancel_checker, publish):
        with self.lock:
            generation = self.generation

        async def run():
            async with http_client(ISSUER, 30) as http:
                device = checked_json(await http.post(ISSUER + "/api/accounts/deviceauth/usercode", json={"client_id": CLIENT_ID}))
                device_id, code = device["device_auth_id"], device["user_code"]
                interval = float(device.get("interval", 5))
                publish({"url": DEVICE_URL, "userCode": code, "message": "在浏览器登录 ChatGPT，并输入此代码。"})
                deadline = time.monotonic() + float(device.get("expires_in", 900))
                while time.monotonic() < deadline:
                    response = await http.post(ISSUER + "/api/accounts/deviceauth/token", json={"device_auth_id": device_id, "user_code": code})
                    if response.status_code in {403, 404}:
                        await asyncio.sleep(interval)
                        continue
                    grant = checked_json(response, secrets=(device_id, code))
                    data = {"grant_type": "authorization_code", "client_id": CLIENT_ID,
                            "redirect_uri": ISSUER + "/deviceauth/callback",
                            "code": grant["authorization_code"], "code_verifier": grant["code_verifier"]}
                    tokens = checked_json(await http.post(ISSUER + "/oauth/token", data=data), secrets=(data["code"], data["code_verifier"]))
                    return credentials(tokens)
                raise ModelError("CODEX_LOGIN_EXPIRED", "登录代码已过期，请重新登录。")

        value = asyncio.run(await_cancellable(run(), cancel_checker))
        with self.lock:
            check_cancelled(cancel_checker)
            if self.closed or generation != self.generation:
                raise OperationCancelled()
            self._save(value)
            self.generation += 1
        return self.public()

    def authorize(self, snapshot, cancel_checker, *, force=False):
        check_cancelled(cancel_checker)
        expires_at = snapshot["tokens"].get("expires_at")
        if not force and (not expires_at or expires_at > time.time()):
            return deepcopy(snapshot["tokens"])
        while not self.refresh_lock.acquire(timeout=.05):
            check_cancelled(cancel_checker)
        try:
            with self.lock:
                check_cancelled(cancel_checker)
                if self.closed or snapshot["generation"] != self.generation or not self.value:
                    raise ModelError("CODEX_LOGIN_CHANGED", "ChatGPT 登录已变化，请重新发起请求。")
                current = deepcopy(self.value)
            if (current["access_token"] != snapshot["tokens"]["access_token"]
                    or (not force and current.get("expires_at", 0) > time.time())):
                return current
            if not current.get("refresh_token"):
                raise ModelError("CODEX_LOGIN_REQUIRED", "ChatGPT 登录已过期，请重新登录。")

            async def refresh():
                async with http_client(ISSUER, 30) as http:
                    response = await http.post(ISSUER + "/oauth/token", data={"grant_type": "refresh_token", "client_id": CLIENT_ID,
                                                                                 "refresh_token": current["refresh_token"]})
                    return credentials(checked_json(response, secrets=tuple(current.get(k, "") for k in ("access_token", "refresh_token", "id_token"))), current)

            value = asyncio.run(await_cancellable(refresh(), cancel_checker))
            with self.lock:
                check_cancelled(cancel_checker)
                if self.closed or snapshot["generation"] != self.generation:
                    raise OperationCancelled()
                self._save(value)
            return value
        finally:
            self.refresh_lock.release()
