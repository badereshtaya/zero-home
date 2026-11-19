"""The Zero account side of the integration (1.3.0, DEVICE_PAIRING_API.md v1) — no Home Assistant imports.

    login (username + password) or QR pairing (pair_start / pair_poll) → a device token (never expires; ends only
    when the account signs this integration out) → home_link_activate (relay key) + ha_agent_config (the settings,
    managed on the account page of Zero's site).
"""
from __future__ import annotations

from typing import Any

import aiohttp

KIND = "ha_integration"

# error kinds (also the config-flow error keys)
ERR_AUTH = "invalid_auth"
ERR_PLAN = "plan"
ERR_RATE = "rate_limited"
ERR_CONNECT = "cannot_connect"
ERR_ANSWER = "bad_answer"
ERR_REJECTED = "rejected"
ERR_2FA = "need_2fa"

_RELAY_KEYS = ("home_id", "agent_token", "relay_ws", "public_url", "subdomain")


class AccountError(Exception):
    def __init__(self, kind: str, message: str = "", status: int = 0) -> None:
        super().__init__(message or kind)
        self.kind = kind
        self.message = message
        self.status = status


class Revoked(AccountError):
    """401: the account signed this integration out (or the password changed) — sign in again."""

    def __init__(self, message: str = "") -> None:
        super().__init__("revoked", message, 401)


def action_url(admin_api: str, action: str) -> str:
    base = (admin_api or "").strip()
    return base + ("&" if "?" in base else "?") + "action=" + action


async def call(session: aiohttp.ClientSession, admin_api: str, action: str, body: dict | None = None, *,
               token: str = "", timeout: float = 20.0) -> tuple[int, dict]:
    """POST one action; (HTTP status, answer). Raises Revoked on 401 when a token was sent, AccountError when
    the server cannot be reached or answers something that is not JSON."""
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Zero-Session"] = token
    try:
        async with session.post(action_url(admin_api, action), json=body or {}, headers=headers,
                                timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
            status = resp.status
            try:
                data = await resp.json(content_type=None)
            except ValueError:
                data = None
    except (aiohttp.ClientError, OSError, TimeoutError) as err:
        raise AccountError(ERR_CONNECT, str(err)) from err
    if not isinstance(data, dict):
        if status == 401 and token:
            raise Revoked("")
        if status in (404, 410):
            # a web layer may answer 404/410 with its own HTML page: «unknown» either way
            return status, {"code": "unknown"}
        raise AccountError(ERR_CONNECT if status >= 500 else ERR_ANSWER, f"HTTP {status}", status)
    if status == 401 and token:
        raise Revoked(str(data.get("message") or data.get("error") or ""))
    return status, data


def _fail(status: int, data: dict) -> AccountError:
    code = str(data.get("code") or "").lower()
    msg = str(data.get("message") or data.get("error") or "")
    if status == 429 or code in ("rate", "rate_limited", "too_many"):
        return AccountError(ERR_RATE, msg, status)
    if code == "plan":
        return AccountError(ERR_PLAN, msg, status)
    if code in ("auth", "invalid_auth") or status in (401, 403):
        return AccountError(ERR_AUTH, msg, status)
    if status >= 500:
        return AccountError(ERR_CONNECT, msg, status)
    return AccountError(ERR_REJECTED, msg, status)


async def login(session, admin_api: str, username: str, password: str, *, device: str, platform: str,
                app_version: str) -> dict:
    """Password sign-in → the login answer (``token`` = the device token)."""
    status, data = await call(session, admin_api, "login", {
        "username": str(username or "").strip(), "password": str(password or ""),
        "device": device, "kind": KIND, "platform": platform, "app_version": app_version})
    if data.get("need_2fa"):
        raise AccountError(ERR_2FA, str(data.get("message") or ""), status)
    if status == 200 and data.get("ok") is True and data.get("token"):
        return data
    raise _fail(status, data)


async def pair_start(session, admin_api: str, *, device_name: str, platform: str, app_version: str) -> dict:
    status, data = await call(session, admin_api, "pair_start", {
        "kind": KIND, "device_name": device_name, "platform": platform, "app_version": app_version})
    if status == 200 and data.get("ok") is True and data.get("pair_id") and data.get("poll_secret"):
        return data
    raise _fail(status, data)


async def pair_poll(session, admin_api: str, pair_id: str, poll_secret: str) -> dict:
    """{status: pending|approved|claimed|denied|expired|unknown, …}"""
    status, data = await call(session, admin_api, "pair_poll", {"pair_id": pair_id, "poll_secret": poll_secret})
    # wrong / unknown pairing: 404 or 410, or code «unknown» → the caller starts a new pairing
    if status in (404, 410) or str(data.get("code") or "").lower() == "unknown" or data.get("status") == "unknown":
        return {"status": "unknown"}
    if status == 200 and data.get("status"):
        return data
    raise _fail(status, data)


async def activate(session, admin_api: str, token: str, *, instance_id: str, ha_version: str,
                   agent_version: str) -> dict:
    """The relay key for this home, with the device token (§5.1)."""
    status, data = await call(session, admin_api, "home_link_activate", {
        "instance_id": instance_id, "ha_version": ha_version, "agent_version": agent_version}, token=token)
    if status == 200 and data.get("ok") is True:
        missing = [k for k in _RELAY_KEYS if not data.get(k)]
        if missing:
            raise AccountError(ERR_ANSWER, "missing " + ", ".join(missing), status)
        return data
    raise _fail(status, data)


async def agent_config(session, admin_api: str, token: str) -> dict:
    """The settings managed on the account page (§5.3)."""
    status, data = await call(session, admin_api, "ha_agent_config", {}, token=token)
    if status == 200 and data.get("ok") is True and isinstance(data.get("config"), dict):
        return data
    raise _fail(status, data)


def options_from_config(cfg: dict) -> dict[str, Any]:
    """ha_agent_config → the integration's internal options (Exposure + the web interface from outside)."""
    conf = (cfg or {}).get("config") or {}
    exp = conf.get("expose") or {}
    mode = str(exp.get("mode") or "all")
    lists = {"expose_areas": [], "expose_domains": [], "expose_labels": [], "expose_entities": []}
    key = {"areas": "expose_areas", "domains": "expose_domains", "entities": "expose_entities"}.get(mode)
    if key:
        lists[key] = [str(x) for x in (exp.get(mode) or []) if x]
    select = key is not None          # a list chosen but empty = nothing is visible (never «everything» by mistake)
    remote = bool(conf.get("remote_ui", False))
    return {"expose_mode": "select" if select else "all", **lists,
            "remote_ui": remote, "set_external_url": remote, "replace_external_url": False}
