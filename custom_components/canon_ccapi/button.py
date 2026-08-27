import os

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import _do_take_photo
from .const import CONF_HOST, CONF_PORT, DEFAULT_SAVE_PATH, DOMAIN, KEY_CONNECTED
from .coordinator import CcapiCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: CcapiCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities([
        CanonTakePhotoButton(hass, coordinator, entry),
        CanonSyncClockButton(coordinator, entry),
    ])


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


class CanonSyncClockButton(CoordinatorEntity, ButtonEntity):
    """Set the camera's clock from Home Assistant.

    A button rather than an action so it is one click on the device page, and
    still callable from an automation via button.press. Non-destructive, so it
    needs no confirmation guard.
    """

    _attr_has_entity_name = True
    _attr_name = "Sync Clock"
    _attr_icon = "mdi:clock-check"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: CcapiCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_sync_clock"
        self._attr_device_info = coordinator.build_device_info(entry)

    @property
    def available(self) -> bool:
        return bool(
            self.coordinator.data and self.coordinator.data.get(KEY_CONNECTED)
        )

    async def async_press(self) -> None:
        await self.coordinator.async_sync_datetime()
