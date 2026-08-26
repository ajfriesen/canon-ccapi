from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfInformation
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    KEY_BATTERY,
    KEY_CONNECTED,
    KEY_LENS,
    KEY_RECORDABLE,
    KEY_STORAGE,
    KEY_TEMPERATURE,
)
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
        CanonStorageTotalSensor(coordinator, entry),
        CanonStorageUsedSensor(coordinator, entry),
        CanonStorageUsedPercentSensor(coordinator, entry),
        CanonStorageFilesSensor(coordinator, entry),
        CanonRecordableShotsSensor(coordinator, entry),
        CanonLensSensor(coordinator, entry),
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


class _CanonStorageBase(CoordinatorEntity, SensorEntity):
    """Shared plumbing for the /devicestatus/storage sensors.

    One poll of that endpoint carries free space, total size and the file
    count, so these all come off data the coordinator already has.
    """

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: CcapiCoordinator, entry: ConfigEntry, key: str
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = coordinator.build_device_info(entry)

    def _card(self) -> dict | None:
        storage = (self.coordinator.data or {}).get(KEY_STORAGE)
        # Only one card slot on the bodies seen so far; index 0 is that card.
        return storage[0] if storage else None

    @property
    def available(self) -> bool:
        return bool(
            self.coordinator.data
            and self.coordinator.data.get(KEY_CONNECTED)
            and self._card()
        )


class CanonStorageSensor(_CanonStorageBase):
    _attr_name = "Storage Free"
    _attr_icon = "mdi:micro-sd"
    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_native_unit_of_measurement = UnitOfInformation.MEGABYTES
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator: CcapiCoordinator, entry: ConfigEntry) -> None:
        # Keeps the original unique_id so existing history is not orphaned.
        super().__init__(coordinator, entry, "storage")

    @property
    def native_value(self):
        card = self._card()
        return None if not card else round(card.get("spacesize", 0) / (1024 * 1024), 1)

    @property
    def extra_state_attributes(self) -> dict:
        card = self._card()
        if not card:
            return {}
        return {
            "card": card.get("name"),
            "access": card.get("accesscapability"),
            "path": card.get("path"),
        }


class CanonStorageTotalSensor(_CanonStorageBase):
    _attr_name = "Storage Total"
    _attr_icon = "mdi:micro-sd"
    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_native_unit_of_measurement = UnitOfInformation.MEGABYTES
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator: CcapiCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "storage_total")

    @property
    def native_value(self):
        card = self._card()
        return None if not card else round(card.get("maxsize", 0) / (1024 * 1024), 1)


class CanonStorageUsedSensor(_CanonStorageBase):
    _attr_name = "Storage Used"
    _attr_icon = "mdi:database"
    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_native_unit_of_measurement = UnitOfInformation.MEGABYTES
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator: CcapiCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "storage_used")

    @property
    def native_value(self):
        card = self._card()
        if not card:
            return None
        used = card.get("maxsize", 0) - card.get("spacesize", 0)
        return round(used / (1024 * 1024), 1)


class CanonStorageUsedPercentSensor(_CanonStorageBase):
    _attr_name = "Storage Used Percent"
    _attr_icon = "mdi:gauge"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator: CcapiCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "storage_used_percent")

    @property
    def native_value(self):
        card = self._card()
        if not card:
            return None
        total = card.get("maxsize") or 0
        if not total:
            return None
        return round((total - card.get("spacesize", 0)) / total * 100, 1)


class CanonStorageFilesSensor(_CanonStorageBase):
    _attr_name = "Files On Card"
    _attr_icon = "mdi:image-multiple"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: CcapiCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "storage_files")

    @property
    def native_value(self):
        card = self._card()
        return None if not card else card.get("contentsnumber")


class CanonRecordableShotsSensor(CoordinatorEntity, SensorEntity):
    """Shots the camera thinks still fit, from /shooting/information/recordable.

    More directly useful than free bytes: it already accounts for the current
    image quality, so it answers "how many more guests" without arithmetic.
    """

    _attr_has_entity_name = True
    _attr_name = "Recordable Shots"
    _attr_icon = "mdi:camera-burst"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: CcapiCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_recordable_shots"
        self._attr_device_info = coordinator.build_device_info(entry)

    def _recordable(self) -> dict | None:
        return (self.coordinator.data or {}).get(KEY_RECORDABLE)

    @property
    def available(self) -> bool:
        return bool(
            self.coordinator.data
            and self.coordinator.data.get(KEY_CONNECTED)
            and self._recordable()
        )

    @property
    def native_value(self):
        return (self._recordable() or {}).get("recordableshots")

    @property
    def extra_state_attributes(self) -> dict:
        # null outside movie mode, so surfaced as an attribute rather than its
        # own permanently-unknown sensor.
        remaining = (self._recordable() or {}).get("remainingtime")
        return {"movie_remaining_time_s": remaining} if remaining is not None else {}


class CanonLensSensor(CoordinatorEntity, SensorEntity):
    """Attached lens, from /devicestatus/lens.

    Refreshed on the slow cadence rather than the 10 s poll - glass does not
    get swapped often enough to justify a request every ten seconds.
    """

    _attr_has_entity_name = True
    _attr_name = "Lens"
    _attr_icon = "mdi:camera-iris"

    def __init__(self, coordinator: CcapiCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_lens"
        self._attr_device_info = coordinator.build_device_info(entry)

    def _lens(self) -> dict:
        return (self.coordinator.data or {}).get(KEY_LENS) or {}

    @property
    def available(self) -> bool:
        return bool(
            self.coordinator.data and self.coordinator.data.get(KEY_CONNECTED)
        )

    @property
    def native_value(self):
        lens = self._lens()
        if not lens:
            return None
        # mount False means no lens attached, and name is then absent or empty.
        if not lens.get("mount"):
            return "none"
        return lens.get("name") or "unknown"

    @property
    def extra_state_attributes(self) -> dict:
        lens = self._lens()
        return {"mounted": lens.get("mount")} if lens else {}
