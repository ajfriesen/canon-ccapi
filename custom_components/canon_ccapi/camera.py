import logging

import aiohttp
from homeassistant.components.camera import Camera
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CCAPI_BASE, CCAPI_VER, CONF_HOST, CONF_PORT, DOMAIN, KEY_BEST_VER, KEY_CAPS, KEY_CONNECTED
from .coordinator import CcapiCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: CcapiCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities([CanonLiveViewCamera(coordinator, entry)])


class CanonLiveViewCamera(CoordinatorEntity, Camera):
    _attr_has_entity_name = True
    _attr_name = "Live View"
    _attr_icon = "mdi:camera-iris"
    # Polling the /flip endpoint every ~1-2 seconds is usually better for CCAPI
    # but 5.0 is a safe default to avoid locking up the camera.
    _attr_frame_interval = 5.0 

    def __init__(self, coordinator: CcapiCoordinator, entry: ConfigEntry) -> None:
        CoordinatorEntity.__init__(self, coordinator)
        Camera.__init__(self)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_liveview"
        self._attr_device_info = coordinator.build_device_info(entry)
        self._liveview_active = False

    @property
    def available(self) -> bool:
        return bool(self.coordinator.data and self.coordinator.data.get(KEY_CONNECTED))

    def _liveview_url(self) -> str:
        host = self._entry.data[CONF_HOST]
        port = self._entry.data[CONF_PORT]
        caps: dict = self.coordinator.data.get(KEY_CAPS, {}) if self.coordinator.data else {}
        path = next(
            (p for p, m in caps.items() if p.endswith("/shooting/liveview") and m.get("post")),
            None,
        )
        if path:
            return f"http://{host}:{port}{path}"
        best_ver = (self.coordinator.data or {}).get(KEY_BEST_VER, CCAPI_VER)
        return f"http://{host}:{port}/{CCAPI_BASE}/{best_ver}/shooting/liveview"

    async def async_will_remove_from_hass(self) -> None:
        if not self._liveview_active:
            return
        url = self._liveview_url()
        try:
            async with aiohttp.ClientSession() as session:
                # Stop live view by setting size to 'off'
                async with session.post(
                    url, 
                    json={"liveviewsize": "off"}, 
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    _LOGGER.debug("Stop live view HTTP %s", resp.status)
        except Exception as exc:
            _LOGGER.debug("Stop live view failed: %s", exc)
        self._liveview_active = False

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        url = self._liveview_url()

        async with aiohttp.ClientSession() as session:
            # 1. Start Live View if it's not active
            if not self._liveview_active:
                try:
                    async with session.post(
                        url,
                        json={"liveviewsize": "small", "cameradisplay": "on"},
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as resp:
                        body = await resp.text()
                        _LOGGER.debug("Live view POST %s → HTTP %s: %s", url, resp.status, body[:200])
                        if resp.status in (200, 201, 409):
                            self._liveview_active = True
                        else:
                            _LOGGER.warning("Live view POST failed: HTTP %s — %s", resp.status, body[:200])
                            return None
                except Exception as exc:
                    _LOGGER.warning("Live view POST failed: %s", exc)
                    return None

            # 2. Fetch the actual frame using the /flip endpoint
            flip_url = f"{url}/flip"
            try:
                async with session.get(
                    flip_url, timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        _LOGGER.warning("Live view GET %s → HTTP %s: %s", flip_url, resp.status, body[:200])
                        # If fetching fails (e.g. camera went to sleep), mark inactive so we try restarting next tick
                        self._liveview_active = False
                        return None
                    
                    # /flip returns image/jpeg directly, no need for multipart parsing
                    return await resp.read()
            except Exception as exc:
                _LOGGER.warning("Live view GET failed: %s", exc)
                self._liveview_active = False

        return None