"""HTTP view for export downloads (spec 15.4).

Authenticated through the HA session (no token in the URL); requires a current admin who
owns the export. The UUID is not a secret link. Responses are attachments and never cached.
"""

from __future__ import annotations

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .core.errors import ErrorCode, SentinelError
from .core.models import is_uuid4

_REGISTERED = f"{DOMAIN}_views_registered"


@callback
def async_register_views(hass: HomeAssistant) -> None:
    # Views cannot be unregistered; they answer 503 while no entry is loaded.
    if hass.data.get(_REGISTERED):
        return
    hass.data[_REGISTERED] = True
    hass.http.register_view(SentinelExportView())


class SentinelExportView(HomeAssistantView):
    url = "/api/ha_sentinel/exports/{export_id}"
    name = "api:ha_sentinel:export"
    requires_auth = True

    async def get(self, request: web.Request, export_id: str) -> web.StreamResponse:
        hass: HomeAssistant = request.app["hass"]
        user = request["hass_user"]
        runtime = hass.data.get(DOMAIN, {}).get("runtime")
        if runtime is None or runtime.data is None:
            return web.Response(status=503, text="not_ready")
        if not is_uuid4(export_id):
            return web.Response(status=404, text="not_found")
        try:
            path = await runtime.data.export_file(export_id, user.id, user.is_admin)
        except SentinelError as err:
            status = 403 if err.code is ErrorCode.FORBIDDEN else 404
            return web.Response(status=status, text=str(err.code))
        return web.FileResponse(path, headers={
            "Content-Disposition": f'attachment; filename="ha-sentinel-export-{export_id[:8]}.zip"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        })
