"""Explicit live view control.

The camera entity starts live view lazily whenever something asks it for a
frame, which means opening a dashboard can leave the stream running. This
switch makes that state visible and lets it be turned off deliberately.
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
    async_add_entities([CanonLiveViewSwitch(coordinator, entry)])


class CanonLiveViewSwitch(CoordinatorEntity, SwitchEntity):
    _attr_has_entity_name = True
    _attr_name = "Live View"
    _attr_icon = "mdi:video"

    def __init__(self, coordinator: CcapiCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_liveview_switch"
        self._attr_device_info = coordinator.build_device_info(entry)

    @property
    def available(self) -> bool:
        return bool(self.coordinator.data and self.coordinator.data.get(KEY_CONNECTED))

    @property
    def is_on(self) -> bool:
        return self.coordinator.liveview_active

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_set_liveview(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_set_liveview(False)
        self.async_write_ha_state()
