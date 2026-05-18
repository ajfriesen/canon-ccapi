from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfInformation
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_HOST, CONF_PORT, DOMAIN, KEY_BATTERY, KEY_CONNECTED, KEY_STORAGE, KEY_TEMPERATURE
from .coordinator import CcapiCoordinator

_BATTERY_LEVEL_MAP = {
    "low": 10,
    "quarter": 25,
    "half": 50,
    "high": 75,
    "full": 100,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: CcapiCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities([
        CanonBatterySensor(coordinator, entry),
        CanonStorageSensor(coordinator, entry),
        CanonTemperatureSensor(coordinator, entry),
    ])


def _device_info(entry: ConfigEntry) -> dict:
    return {
        "identifiers": {(DOMAIN, entry.entry_id)},
        "name": f"Canon Camera ({entry.data[CONF_HOST]}:{entry.data[CONF_PORT]})",
        "manufacturer": "Canon",
    }


class CanonBatterySensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Battery"
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: CcapiCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_battery"
        self._attr_device_info = _device_info(entry)

    @property
    def available(self) -> bool:
        return bool(
            self.coordinator.data
            and self.coordinator.data.get(KEY_CONNECTED)
            and self.coordinator.data.get(KEY_BATTERY) is not None
        )

    @property
    def native_value(self):
        if not self.coordinator.data:
            return None
        battery = self.coordinator.data.get(KEY_BATTERY)
        if not battery:
            return None
        return _BATTERY_LEVEL_MAP.get(battery.get("level", ""))


class CanonTemperatureSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Temperature Status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["normal", "warning", "error"]
    _attr_icon = "mdi:thermometer"

    def __init__(self, coordinator: CcapiCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_temperature"
        self._attr_device_info = _device_info(entry)

    @property
    def available(self) -> bool:
        return bool(
            self.coordinator.data
            and self.coordinator.data.get(KEY_CONNECTED)
            and self.coordinator.data.get(KEY_TEMPERATURE) is not None
        )

    @property
    def native_value(self):
        if not self.coordinator.data:
            return None
        temp = self.coordinator.data.get(KEY_TEMPERATURE)
        if not temp:
            return None
        return temp.get("status")


class CanonStorageSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Storage Free"
    _attr_native_unit_of_measurement = UnitOfInformation.MEGABYTES
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: CcapiCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_storage"
        self._attr_device_info = _device_info(entry)

    @property
    def available(self) -> bool:
        return bool(
            self.coordinator.data
            and self.coordinator.data.get(KEY_CONNECTED)
            and self.coordinator.data.get(KEY_STORAGE)
        )

    @property
    def native_value(self):
        if not self.coordinator.data:
            return None
        storage = self.coordinator.data.get(KEY_STORAGE)
        if not storage:
            return None
        space_bytes = storage[0].get("spacesize", 0)
        return round(space_bytes / (1024 * 1024), 1)
