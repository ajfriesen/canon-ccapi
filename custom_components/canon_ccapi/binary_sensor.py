from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_HOST, CONF_PORT, DOMAIN, KEY_CONNECTED


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities([CanonConnectionSensor(coordinator, entry)])


class CanonConnectionSensor(CoordinatorEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_has_entity_name = True
    _attr_name = "Connection"

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_connection"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": f"Canon Camera ({entry.data[CONF_HOST]}:{entry.data[CONF_PORT]})",
            "manufacturer": "Canon",
            "configuration_url": f"http://{entry.data[CONF_HOST]}:{entry.data[CONF_PORT]}/ccapi",
        }

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.data and self.coordinator.data.get(KEY_CONNECTED))
