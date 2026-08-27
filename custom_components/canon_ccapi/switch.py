"""Explicit camera display control.

The camera entity starts live view lazily whenever something asks it for a
frame, and starting live view blanks the camera's own screen. This switch
makes that state visible from the screen's point of view: on means the rear
display is showing the scene, off means the preview is being streamed to Home
Assistant instead.
"""

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, KEY_CONNECTED
from .coordinator import CcapiCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: CcapiCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities([CanonCameraDisplaySwitch(coordinator, entry)])


class CanonCameraDisplaySwitch(CoordinatorEntity, SwitchEntity):
    _attr_has_entity_name = True
    _attr_name = "Camera Display"
    _attr_icon = "mdi:monitor"

    def __init__(self, coordinator: CcapiCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_cameradisplay_switch"
        self._attr_device_info = coordinator.build_device_info(entry)

    @property
    def available(self) -> bool:
        return bool(self.coordinator.data and self.coordinator.data.get(KEY_CONNECTED))

    @property
    def is_on(self) -> bool:
        """The rear screen is on exactly when live view is not streaming."""
        return not self.coordinator.liveview_active

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_set_liveview(False)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_set_liveview(True)
        self.async_write_ha_state()
