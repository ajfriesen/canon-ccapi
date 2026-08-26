"""Writable camera settings from CCAPI /functions/, exposed as selects.

Covers displayoff, autopoweroff and beep. All three share the same shape:
GET returns {"value": ..., "ability": [...]} and PUT takes {"value": ...}.
"""

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, KEY_CONNECTED, KEY_SETTINGS
from .coordinator import CcapiCoordinator

# Fallback option lists, used only until the camera reports its own "ability"
# (e.g. it was offline when the entity was created). The camera always wins.
# displayoff and autopoweroff are in SECONDS of inactivity.
_SETTINGS: dict[str, dict] = {
    "displayoff": {
        "name": "Display off after",
        "icon": "mdi:monitor-off",
        "fallback": ["10", "20", "30", "60", "120"],
    },
    "autopoweroff": {
        "name": "Auto power off after",
        "icon": "mdi:power-sleep",
        "fallback": ["30", "60", "180", "300", "600", "disable"],
    },
    "beep": {
        "name": "Beep",
        "icon": "mdi:volume-high",
        "fallback": ["enable", "disable", "disabletouch"],
    },
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: CcapiCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    # Created unconditionally so the entities do not vanish when the camera is
    # merely offline; they report unavailable instead.
    async_add_entities(
        CanonSettingSelect(coordinator, entry, key) for key in _SETTINGS
    )


class CanonSettingSelect(CoordinatorEntity, SelectEntity):
    _attr_has_entity_name = True

    def __init__(
        self, coordinator: CcapiCoordinator, entry: ConfigEntry, key: str
    ) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_name = _SETTINGS[key]["name"]
        self._attr_icon = _SETTINGS[key]["icon"]
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = coordinator.build_device_info(entry)

    def _setting(self) -> dict | None:
        return ((self.coordinator.data or {}).get(KEY_SETTINGS) or {}).get(self._key)

    @property
    def available(self) -> bool:
        return bool(
            self.coordinator.data
            and self.coordinator.data.get(KEY_CONNECTED)
            and self._setting() is not None
        )

    @property
    def options(self) -> list[str]:
        setting = self._setting() or {}
        ability = setting.get("ability")
        if isinstance(ability, list) and ability:
            opts = [str(a) for a in ability]
        else:
            opts = list(_SETTINGS[self._key]["fallback"])
        # HA rejects a current_option that is not in options. Some bodies report
        # a value outside their own ability list, so keep it selectable rather
        # than letting the entity throw.
        current = setting.get("value")
        if current is not None and str(current) not in opts:
            opts.append(str(current))
        return opts

    @property
    def current_option(self) -> str | None:
        value = (self._setting() or {}).get("value")
        return None if value is None else str(value)

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_setting(self._key, option)
