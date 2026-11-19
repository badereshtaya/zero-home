"""Problems of this integration → Zero Admin (action client_log, 1.3.2) — no Home Assistant imports.

Only warnings and errors of the integration's own logger, plus relay connect / disconnect events. Batched
(≤ 200 lines a request), the same message counted instead of repeated, quiet when the endpoint is not there
(404), backing off on 429. Never tokens or request bodies: long secret-looking strings are cut out.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable

import aiohttp

from .account import action_url

MAX_QUEUE = 400
MAX_BATCH = 200
_SECRET = re.compile(r"(?:[A-Za-z0-9+/_\-]{32,}={0,2})|(?:(?:token|password|secret|session|auth|bearer)[^\s,;&]*[=:\s]\s*\S+)", re.I)


def clean(s: str, limit: int) -> str:
    t = _SECRET.sub("…", str(s or ""))
    return t if len(t) <= limit else t[:limit] + "…"


class ClientLog:
    def __init__(self, admin_api: str, token: Callable[[], str], *, device: str, device_id: str, version: str,
                 now: Callable[[], float] = time.time) -> None:
        self.admin_api, self.token, self.device, self.device_id, self.version = admin_api, token, device, device_id, version
        self.now = now
        self.queue: list[dict] = []
        self._seen: dict[str, dict] = {}
        self._quiet_until = 0.0
        self.off = False

    def add(self, level: str, msg: str, *, stack: str = "", ctx: dict | None = None) -> None:
        if self.off or not str(msg).strip():
            return
        m = clean(msg, 500)
        key = f"{level}|{m}"
        old = self._seen.get(key)
        if old is not None and old in self.queue:
            old.setdefault("ctx", {})["repeat"] = old["ctx"].get("repeat", 1) + 1
            return
        line: dict[str, Any] = {"t": datetime.now(timezone.utc).isoformat(timespec="seconds"), "level": level, "msg": m,
                                "ctx": dict(ctx or {})}
        if stack:
            line["stack"] = clean(stack, 2000)
        self._seen[key] = line
        self.queue.append(line)
        if len(self.queue) > MAX_QUEUE:
            del self.queue[: len(self.queue) - MAX_QUEUE]
        if len(self._seen) > 2000:
            self._seen = {k: v for k, v in self._seen.items() if v in self.queue}

    async def flush(self, session: aiohttp.ClientSession) -> int:
        """Send one batch; returns how many lines left the queue."""
        if self.off or not self.queue or self.now() < self._quiet_until:
            return 0
        batch = self.queue[:MAX_BATCH]
        headers = {"Content-Type": "application/json"}
        tok = self.token() or ""
        if tok:
            headers["X-Zero-Session"] = tok
        body = {"channel": "ha", "device": self.device, "device_id": self.device_id, "version": self.version,
                "page": "integration", "lines": batch}
        try:
            async with session.post(action_url(self.admin_api, "client_log"), json=body, headers=headers,
                                    timeout=aiohttp.ClientTimeout(total=15)) as resp:
                status = resp.status
                await resp.read()
        except (aiohttp.ClientError, OSError, asyncio.TimeoutError):
            self._quiet_until = self.now() + 120
            return 0
        if status == 200:
            del self.queue[: len(batch)]
            return len(batch)
        if status == 404:
            self.off = True                     # not deployed: stay quiet until the next start
        elif status == 429:
            self._quiet_until = self.now() + 600
        elif status == 401 and tok:
            self._quiet_until = self.now() + 300   # signed out: the integration says so itself
        return 0


class LogHandler(logging.Handler):
    """Warnings and errors of the integration's own loggers → ClientLog (thread-safe through `call`)."""

    def __init__(self, log: ClientLog, call: Callable[[Callable[[], None]], Any]) -> None:
        super().__init__(level=logging.WARNING)
        self.log, self.call = log, call

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
            stack = logging.Formatter().formatException(record.exc_info) if record.exc_info else ""
            level = "error" if record.levelno >= logging.ERROR else "warn"
            ctx = {"logger": record.name}
            self.call(lambda: self.log.add(level, msg, stack=stack, ctx=ctx))
        except Exception:  # noqa: BLE001 - logging must never raise
            pass
