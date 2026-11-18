"""The home side of public traffic: talk to Home Assistant's own HTTP server
(PROTOCOL.md §3.1) and check that it trusts us as a proxy (§3.3).

The aiohttp session handed in MUST be created with ``auto_decompress=False``
(bodies are passed through byte for byte, Content-Encoding included) and a
dummy cookie jar (cookies belong to the browser, not to the agent).
"""
from __future__ import annotations

from typing import AsyncIterator, Iterable, Sequence

import aiohttp
from yarl import URL

from .frames import HOP_BY_HOP

# request headers the agent always replaces or drops
_DROP_REQUEST = frozenset({
    "host", "x-forwarded-for", "x-forwarded-proto", "x-forwarded-host",
    "x-forwarded-port", "x-forwarded-server", "x-forwarded-prefix", "forwarded",
    "x-real-ip", "content-length-override",
})
# extra request headers dropped on WebSocket opens (aiohttp makes its own handshake)
_DROP_WS = frozenset({
    "sec-websocket-key", "sec-websocket-version", "sec-websocket-extensions",
    "sec-websocket-protocol", "sec-websocket-accept", "content-length",
})

# a request that needs no authentication (no ban counting): any 404 is fine
PROBE_PATH = "/.zero_home_probe"
PROBE_CLIENT = "192.0.2.1"     # TEST-NET-1, PROTOCOL §3.3

HeaderList = Sequence[Sequence[str]]


def _connection_tokens(headers: HeaderList) -> set[str]:
    out: set[str] = set()
    for name, value in headers:
        if str(name).lower() == "connection":
            out.update(t.strip().lower() for t in str(value).split(",") if t.strip())
    return out


def frame_ancestors(zero_web_origin: str) -> str:
    origin = (zero_web_origin or "").strip().rstrip("/")
    return "frame-ancestors 'self'" + (f" {origin}" if origin else "")


def rewrite_request_headers(headers: HeaderList, client_ip: str, public_host: str,
                            *, websocket: bool = False) -> list[tuple[str, str]]:
    """Headers for the request to the local Home Assistant (§3.1)."""
    extra = _connection_tokens(headers)
    out: list[tuple[str, str]] = []
    for pair in headers or ():
        if len(pair) != 2:
            continue
        name, value = str(pair[0]), str(pair[1])
        low = name.lower()
        if low in HOP_BY_HOP or low in _DROP_REQUEST or low in extra:
            continue
        if websocket and low in _DROP_WS:
            continue
        if "\r" in name or "\n" in name or "\r" in value or "\n" in value:
            continue
        out.append((name, value))
    out.append(("X-Forwarded-For", client_ip or "0.0.0.0"))
    out.append(("X-Forwarded-Proto", "https"))
    if public_host:
        out.append(("X-Forwarded-Host", public_host))
    return out


def _strip_frame_ancestors(csp: str) -> str:
    parts = [p.strip() for p in csp.split(";")]
    keep = [p for p in parts if p and not p.lower().startswith("frame-ancestors")]
    return "; ".join(keep)


def rewrite_response_headers(raw: Iterable[tuple[str, str]], zero_web_origin: str) -> list[list[str]]:
    """Headers of the answer going back to the relay (§3.1).

    Removes hop-by-hop headers and ``X-Frame-Options``; keeps every other
    header (duplicates such as Set-Cookie included); any ``frame-ancestors``
    directive already present is dropped and one
    ``Content-Security-Policy: frame-ancestors 'self' <zero_web_origin>`` is added.
    """
    raw = [(str(n), str(v)) for n, v in raw]
    extra = _connection_tokens(raw)
    out: list[list[str]] = []
    for name, value in raw:
        low = name.lower()
        if low in HOP_BY_HOP or low in extra or low == "x-frame-options":
            continue
        if low == "content-security-policy":
            value = _strip_frame_ancestors(value)
            if not value:
                continue
        out.append([name, value])
    out.append(["Content-Security-Policy", frame_ancestors(zero_web_origin)])
    return out


def raw_headers_of(resp) -> list[tuple[str, str]]:
    """Response headers as (name, value) with duplicates kept."""
    raw = getattr(resp, "raw_headers", None)
    if raw:
        return [(n.decode("latin-1"), v.decode("latin-1")) for n, v in raw]
    return list(resp.headers.items())


class BadPath(ValueError):
    """The relay sent a path that is not an absolute local path."""


class LocalHA:
    """Home Assistant's own HTTP server, as seen from the integration."""

    def __init__(self, session: aiohttp.ClientSession, base_url: str, *,
                 public_host: str = "", zero_web_origin: str = "",
                 connect_timeout: float = 10.0) -> None:
        self.session = session
        self.base_url = base_url.rstrip("/")
        self.public_host = public_host
        self.zero_web_origin = zero_web_origin
        self.connect_timeout = connect_timeout

    # -- urls ---------------------------------------------------------------
    def url(self, path: str, *, websocket: bool = False) -> URL:
        if not isinstance(path, str) or not path.startswith("/") or path.startswith("//"):
            raise BadPath(f"bad path {path!r}")
        if any(c in path for c in "\r\n\t #\\"):
            raise BadPath(f"bad path {path!r}")
        base = self.base_url
        if websocket:
            base = "ws" + base[4:] if base.startswith("http") else base
        return URL(base + path, encoded=True)

    # -- http ---------------------------------------------------------------
    async def request(self, method: str, path: str, headers: HeaderList, client_ip: str,
                      body: AsyncIterator[bytes] | None) -> aiohttp.ClientResponse:
        """Open the request; the caller reads and releases the response."""
        hdrs = rewrite_request_headers(headers, client_ip, self.public_host)
        return await self.session.request(
            method.upper(), self.url(path),
            headers=hdrs,
            data=body,
            allow_redirects=False,
            ssl=False,
            skip_auto_headers=("Accept-Encoding", "User-Agent", "Accept", "Content-Type"),
            timeout=aiohttp.ClientTimeout(total=None, sock_connect=self.connect_timeout),
        )

    def response_headers(self, resp) -> list[list[str]]:
        return rewrite_response_headers(raw_headers_of(resp), self.zero_web_origin)

    # -- websocket ------------------------------------------------------------
    async def websocket(self, path: str, headers: HeaderList, client_ip: str,
                        protocols: Sequence[str] = ()) -> aiohttp.ClientWebSocketResponse:
        hdrs = rewrite_request_headers(headers, client_ip, self.public_host, websocket=True)
        return await self.session.ws_connect(
            self.url(path, websocket=True),
            headers=hdrs,
            protocols=tuple(protocols or ()),
            autoping=True,
            heartbeat=None,
            max_msg_size=0,
            ssl=False,
            timeout=aiohttp.ClientWSTimeout(ws_close=10.0) if hasattr(aiohttp, "ClientWSTimeout") else 10.0,
        )

    # -- §3.3 -------------------------------------------------------------------
    async def check_ui_ready(self) -> bool | None:
        """True/False per §3.3, None when Home Assistant could not be reached."""
        hdrs = [("X-Forwarded-For", PROBE_CLIENT), ("X-Forwarded-Proto", "https")]
        if self.public_host:
            hdrs.append(("X-Forwarded-Host", self.public_host))
        try:
            async with self.session.get(
                self.url(PROBE_PATH), headers=hdrs, allow_redirects=False, ssl=False,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                await resp.read()
                return resp.status != 400
        except (aiohttp.ClientError, TimeoutError, OSError):
            return None
