"""Sidebar panel registration (spec 14.1). Isolated here as a compatibility adapter.

The bundled JS contains no installation data, so serving it as a static file does not
expose configuration. The panel is admin-only; the backend still enforces admin on
every command because hiding a panel is not access control.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

from ..const import DOMAIN

PANEL_URL_PATH = "ha-sentinel"
WEBCOMPONENT = "ha-sentinel-panel"
STATIC_URL = f"/{DOMAIN}_static"
BUNDLE = "ha-sentinel.js"
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
_STATIC_REGISTERED = f"{DOMAIN}_static_registered"


def _bundle_hash() -> str:
    return hashlib.sha256((FRONTEND_DIR / BUNDLE).read_bytes()).hexdigest()[:12]


async def async_register_panel(hass: HomeAssistant, version: str) -> None:
    # Static paths cannot be unregistered, so register them once per HA run.
    if not hass.data.get(_STATIC_REGISTERED):
        await hass.http.async_register_static_paths(
            [StaticPathConfig(STATIC_URL, str(FRONTEND_DIR), cache_headers=False)]
        )
        hass.data[_STATIC_REGISTERED] = True
    # Cache busting by content hash; reading the file must not block the event loop.
    digest = await hass.async_add_executor_job(_bundle_hash)
    if frontend.async_panel_exists(hass, PANEL_URL_PATH):
        frontend.async_remove_panel(hass, PANEL_URL_PATH, warn_if_unknown=False)
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL_PATH,
        webcomponent_name=WEBCOMPONENT,
        sidebar_title="Sentinel",
        sidebar_icon="mdi:shield-check-outline",
        module_url=f"{STATIC_URL}/{BUNDLE}?v={digest}",
        require_admin=True,
        config={"version": version},
    )


def async_remove_panel(hass: HomeAssistant) -> None:
    frontend.async_remove_panel(hass, PANEL_URL_PATH, warn_if_unknown=False)
