import os

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_HOST, CONF_PORT, DEFAULT_SAVE_PATH, DOMAIN, KEY_CONNECTED

from .coordinator import CcapiCoordinator
from . import _do_take_photo


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: CcapiCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities([CanonTakePhotoButton(hass, coordinator, entry)])


class CanonTakePhotoButton(CoordinatorEntity, ButtonEntity):
    _attr_has_entity_name = True
    _attr_name = "Take Photo"
    _attr_icon = "mdi:camera"

    def __init__(
        self, hass: HomeAssistant, coordinator: CcapiCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator)
        self._hass = hass
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_take_photo"
        self._attr_device_info = coordinator.build_device_info(entry)

    @property
    def available(self) -> bool:
        return bool(
            self.coordinator.data and self.coordinator.data.get(KEY_CONNECTED)
        )

    async def async_press(self) -> None:
        host = self._entry.data[CONF_HOST]
        port = self._entry.data[CONF_PORT]
        save_path = os.path.join(
            self._hass.config.media_dirs.get("local", "/media/local"), DEFAULT_SAVE_PATH
        )
        await _do_take_photo(
            self._hass, host, port, save_path, autofocus=True, delete_from_camera=False
        )
