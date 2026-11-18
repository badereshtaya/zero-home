"""Wire format of the agent connection (PROTOCOL.md §2).

Control messages are JSON text frames ``{"t": "<type>", ...}``.
Bodies and binary WebSocket messages are binary frames::

    | stream id: uint32 big-endian | kind: uint8 | payload... |
"""
from __future__ import annotations

import datetime as _dt
import json
import random
import struct
from typing import Any, Callable

KIND_BODY = 0       # body chunk
KIND_END = 1        # body end (payload empty)
KIND_WS_BINARY = 2  # binary WebSocket message

CHUNK_MAX = 64 * 1024           # body chunk size limit (§2.1)
PING_INTERVAL = 25.0            # keep-alive ping (§2)
DEAD_AFTER = 60.0               # no traffic for this long = dead (§2)
RPC_TIMEOUT = 14.0              # relay waits 15 s; answer just before it

# reconnect schedule (§2): 1 s, 2 s, 5 s, 10 s, 30 s, then every 60 s, ±20 % jitter
BACKOFF_STEPS = (1.0, 2.0, 5.0, 10.0, 30.0)
BACKOFF_FOREVER = 60.0
BACKOFF_JITTER = 0.20

# bye reasons that mean "stop until the integration is reloaded"
FINAL_BYE = frozenset({"revoked", "plan"})

# hop-by-hop headers (RFC 7230 §6.1 + the ones PROTOCOL §2.1 lists)
HOP_BY_HOP = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "proxy-connection", "te", "trailer", "trailers", "transfer-encoding", "upgrade",
})

_HEAD = struct.Struct(">IB")


def pack(stream_id: int, kind: int, payload: bytes = b"") -> bytes:
    """One binary frame."""
    if not 0 <= stream_id <= 0xFFFFFFFF:
        raise ValueError("stream id out of range")
    return _HEAD.pack(stream_id, kind) + bytes(payload)


def unpack(data: bytes) -> tuple[int, int, bytes]:
    """(stream id, kind, payload) of one binary frame."""
    if len(data) < _HEAD.size:
        raise ValueError("binary frame shorter than its header")
    sid, kind = _HEAD.unpack_from(data, 0)
    return sid, kind, bytes(data[_HEAD.size:])


def _default(obj: Any) -> Any:
    if isinstance(obj, (_dt.datetime, _dt.date, _dt.time)):
        return obj.isoformat()
    if isinstance(obj, (set, frozenset, tuple)):
        return list(obj)
    if isinstance(obj, bytes):
        return obj.decode("utf-8", "replace")
    if hasattr(obj, "as_dict"):
        return obj.as_dict()
    return str(obj)


def dumps(msg: dict) -> str:
    """A control message as compact JSON (Arabic stays readable)."""
    return json.dumps(msg, ensure_ascii=False, separators=(",", ":"), default=_default)


def loads(text: str) -> dict:
    msg = json.loads(text)
    if not isinstance(msg, dict) or not isinstance(msg.get("t"), str):
        raise ValueError("control message without a type")
    return msg


def backoff_delay(attempt: int, rand: Callable[[], float] = random.random) -> float:
    """Delay before reconnect number ``attempt`` (0-based), with ±20 % jitter."""
    base = BACKOFF_STEPS[attempt] if 0 <= attempt < len(BACKOFF_STEPS) else BACKOFF_FOREVER
    return base * (1.0 + BACKOFF_JITTER * (2.0 * rand() - 1.0))


def chunks(data: bytes, size: int = CHUNK_MAX):
    """Split bytes into chunks no larger than ``size``."""
    for i in range(0, len(data), size):
        yield data[i:i + size]
