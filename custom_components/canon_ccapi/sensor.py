from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfInformation
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, KEY_BATTERY, KEY_CONNECTED, KEY_STORAGE, KEY_TEMPERATURE
from .coordinator import CcapiCoordinator

_BATTERY_LEVEL_MAP = {
    "low": 10,
    "quarter": 25,
    "half": 50,
    "high": 75,
    "full": 100,
}

_BATTERY_KIND_LABELS = {
    "battery": "Battery",
    "not_inserted": "Not Inserted",
    "ac_adapter": "AC Adapter",
    "dc_coupler": "DC Coupler",
    "unknown": "Unknown",
    "batterygrip": "Battery Grip",
}

_BATTERY_KIND_ICONS = {
    "ac_adapter": "mdi:power-plug",
    "dc_coupler": "mdi:power-plug-outline",
    "not_inserted": "mdi:battery-off",
    "unknown": "mdi:battery-unknown",
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




class CanonBatterySensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Battery"
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: CcapiCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_battery"
        self._attr_device_info = coordinator.build_device_info(entry)

    def _battery(self) -> dict | None:
        return (self.coordinator.data or {}).get(KEY_BATTERY)

    @property
    def available(self) -> bool:
        return bool(
            self.coordinator.data
            and self.coordinator.data.get(KEY_CONNECTED)
            and self._battery() is not None
        )

    @property
    def icon(self) -> str | None:
        battery = self._battery()
        if not battery:
            return None
        return _BATTERY_KIND_ICONS.get(battery.get("kind", ""))

    @property
    def native_value(self):
        battery = self._battery()
        if not battery:
            return None
        kind = battery.get("kind", "")
        if kind != "battery":
            return None
        return _BATTERY_LEVEL_MAP.get(battery.get("level", ""))

    @property
    def extra_state_attributes(self) -> dict:
        battery = self._battery()
        if not battery:
            return {}
        kind = battery.get("kind", "")
        attrs = {
            "kind": _BATTERY_KIND_LABELS.get(kind, kind),
            "name": battery.get("name", ""),
        }
        quality = battery.get("quality", "")
        if quality:
            attrs["quality"] = quality
        return attrs


_TEMPERATURE_STATUS_DESCRIPTIONS = {
    "normal": "Normal status",
    "warning": "Warning indication status",
    "frameratedown": "Reduced frame rate",
    "disableliveview": "Live View prohibited",
    "disablerelease": "Shooting prohibited",
    "stillqualitywarning": "Degraded still image quality warning",
    "restrictionmovierecording": "Movie recording restricted",
    "warning_and_restrictionmovierecording": "Warning and movie recording restricted",
    "frameratedown_and_restrictionmovierecording": "Reduced frame rate and movie recording restricted",
    "disableliveview_and_restrictionmovierecording": "Live View prohibited and movie recording restricted",
    "disablerelease_and_restrictionmovierecording": "Shooting prohibited and movie recording restricted",
    "stillqualitywarning_and_restrictionmovierecording": "Degraded still image quality and movie recording restricted",
}


class CanonTemperatureSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Temperature Status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [
        "normal",
        "warning",
        "frameratedown",
        "disableliveview",
        "disablerelease",
        "stillqualitywarning",
        "restrictionmovierecording",
        "warning_and_restrictionmovierecording",
        "frameratedown_and_restrictionmovierecording",
        "disableliveview_and_restrictionmovierecording",
        "disablerelease_and_restrictionmovierecording",
        "stillqualitywarning_and_restrictionmovierecording",
    ]
    _attr_icon = "mdi:thermometer"

    def __init__(self, coordinator: CcapiCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_temperature"
        self._attr_device_info = coordinator.build_device_info(entry)

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

    @property
    def extra_state_attributes(self) -> dict:
        if not self.coordinator.data:
            return {}
        temp = self.coordinator.data.get(KEY_TEMPERATURE)
        if not temp:
            return {}
        status = temp.get("status", "")
        description = _TEMPERATURE_STATUS_DESCRIPTIONS.get(status)
        if description:
            return {"description": description}
        return {}


class CanonStorageSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Storage Free"
    _attr_native_unit_of_measurement = UnitOfInformation.MEGABYTES
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: CcapiCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_storage"
        self._attr_device_info = coordinator.build_device_info(entry)

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
