import logging

import aiohttp
from homeassistant.components.camera import Camera
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
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
    # Measured on an EOS R100: /flip answers in 55-91 ms for a ~72 KB frame,
    # and 80 back-to-back fetches showed no errors and no degradation. The
    # previous 5.0 was ~75x more conservative than the camera needs; 1 fps is
    # still 15x under what it sustained.
    _attr_frame_interval = 1.0

    def __init__(self, coordinator: CcapiCoordinator, entry: ConfigEntry) -> None:
        CoordinatorEntity.__init__(self, coordinator)
        Camera.__init__(self)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_liveview"
        self._attr_device_info = coordinator.build_device_info(entry)

    @property
    def available(self) -> bool:
        return bool(self.coordinator.data and self.coordinator.data.get(KEY_CONNECTED))

    async def async_will_remove_from_hass(self) -> None:
        if self.coordinator.liveview_active:
            await self.coordinator.async_set_liveview(False)

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        if not self.coordinator.liveview_active:
            if not await self.coordinator.async_set_liveview(True):
                return None

        # Shared session: at 1 fps a per-frame ClientSession would mean a fresh
        # TCP handshake for every frame. Keep-alive was measured to remove the
        # connect cost entirely after the first fetch.
        session = async_get_clientsession(self.hass)
        flip_url = f"{self.coordinator.liveview_url()}/flip"
        try:
            async with session.get(
                flip_url, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    _LOGGER.warning(
                        "Live view GET %s -> HTTP %s: %s", flip_url, resp.status, body[:200]
                    )
                    # Camera slept or something else stopped the stream - forget
                    # the flag so the next frame restarts it.
                    self.coordinator.note_liveview_stopped()
                    return None
                # /flip returns image/jpeg directly, no multipart parsing needed
                return await resp.read()
        except Exception as exc:  # noqa: BLE001 - one bad frame must not raise
            _LOGGER.warning("Live view GET failed: %s", exc)
            self.coordinator.note_liveview_stopped()
        return None
