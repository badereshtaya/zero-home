"""Activation with Zero Admin (PROTOCOL.md §1)."""
from __future__ import annotations

import re

import aiohttp

ACTION = "home_link_activate"
_REQUIRED = ("home_id", "agent_token", "relay_ws", "public_url", "subdomain")
_CODE_RE = re.compile(r"^ZH([A-Z0-9]{4})([A-Z0-9]{4})([A-Z0-9]{4})$")

# error kinds (they are also the config-flow error keys)
ERR_FORMAT = "invalid_format"
ERR_REJECTED = "rejected"          # 422 with the server's own (Arabic) message
ERR_INVALID = "code_invalid"
ERR_USED = "code_used"
ERR_EXPIRED = "code_expired"
ERR_PLAN = "plan"
ERR_RATE = "rate_limited"
ERR_CONNECT = "cannot_connect"
ERR_ANSWER = "bad_answer"
ERR_AUTH = "invalid_auth"        # the Zero username / password (linking with the account, no code)

# optional machine code in a 422 answer (not in PROTOCOL §1; see NOTES.md)
_SERVER_CODES = {"invalid": ERR_INVALID, "unknown": ERR_INVALID, "used": ERR_USED,
                 "expired": ERR_EXPIRED, "plan": ERR_PLAN, "rate": ERR_RATE,
                 "rate_limited": ERR_RATE, "too_many": ERR_RATE, "auth": ERR_AUTH}


class ActivationError(Exception):
    def __init__(self, kind: str, message: str = "") -> None:
        super().__init__(message or kind)
        self.kind = kind
        self.message = message


def normalize_code(raw: str) -> str:
    """``zh 7k2q 9mwd-4xrt`` → ``ZH-7K2Q-9MWD-4XRT``; raises ActivationError."""
    s = re.sub(r"[\s\-_‐-―−]+", "", str(raw or "")).upper()
    m = _CODE_RE.match(s)
    if not m:
        raise ActivationError(ERR_FORMAT)
    return "ZH-" + "-".join(m.groups())


def action_url(admin_api: str, action: str = ACTION) -> str:
    base = (admin_api or "").strip()
    return base + ("&" if "?" in base else "?") + "action=" + action


async def activate(session: aiohttp.ClientSession, admin_api: str, code: str, *,
                   instance_id: str, ha_version: str, agent_version: str,
                   timeout: float = 20.0) -> dict:
    """Exchange the code for the home key. Returns the server's answer (dict)."""
    payload = {"code": normalize_code(code), "instance_id": instance_id,
               "ha_version": ha_version, "agent_version": agent_version}
    return await _post(session, admin_api, payload, timeout)


async def activate_login(session: aiohttp.ClientSession, admin_api: str, username: str, password: str, *,
                         instance_id: str, ha_version: str, agent_version: str,
                         timeout: float = 20.0) -> dict:
    """Link the home with the Zero account itself (no code): the address becomes https://<username>.home.<domain>."""
    username = str(username or "").strip()
    if not username or not password:
        raise ActivationError(ERR_AUTH)
    payload = {"username": username, "password": str(password), "instance_id": instance_id,
               "ha_version": ha_version, "agent_version": agent_version}
    return await _post(session, admin_api, payload, timeout)


async def _post(session: aiohttp.ClientSession, admin_api: str, payload: dict, timeout: float) -> dict:
    try:
        async with session.post(action_url(admin_api), json=payload,
                                timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
            status = resp.status
            try:
                body = await resp.json(content_type=None)
            except ValueError:
                body = None
    except (aiohttp.ClientError, OSError, TimeoutError) as err:
        raise ActivationError(ERR_CONNECT, str(err)) from err
    if not isinstance(body, dict):
        raise ActivationError(ERR_CONNECT if status >= 500 else ERR_ANSWER, f"HTTP {status}")
    if status == 200 and body.get("ok") is True:
        missing = [k for k in _REQUIRED if not body.get(k)]
        if missing:
            raise ActivationError(ERR_ANSWER, "missing " + ", ".join(missing))
        return body
    message = str(body.get("error") or "")
    kind = _SERVER_CODES.get(str(body.get("code") or "").lower())
    if kind is None:
        if status == 429:
            kind = ERR_RATE
        elif status >= 500:
            kind = ERR_CONNECT
        else:
            kind = ERR_REJECTED
    raise ActivationError(kind, message)
