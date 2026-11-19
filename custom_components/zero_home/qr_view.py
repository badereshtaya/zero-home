"""The QR image of the sign-in screen (served only while a sign-in flow shows it; the key is random)."""
from __future__ import annotations

from aiohttp import web
from homeassistant.components.http import HomeAssistantView

from .const import DOMAIN, QR_PATH


class ZeroHomeQrView(HomeAssistantView):
    url = QR_PATH + "/{key}.svg"
    name = "api:zero_home:qr"
    requires_auth = False          # an <img> in the flow cannot send the token; the key is a one-time random id

    async def get(self, request: web.Request, key: str) -> web.Response:
        svg = request.app["hass"].data.get(DOMAIN, {}).get("qr", {}).get(key)
        if not svg:
            return web.Response(status=404)
        return web.Response(body=svg, content_type="image/svg+xml", headers={"Cache-Control": "no-store"})
