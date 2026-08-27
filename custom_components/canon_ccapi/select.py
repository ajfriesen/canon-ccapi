"""Writable camera settings exposed as selects.

Covers the /functions/ settings (displayoff, autopoweroff, beep) and the
shooting settings (ISO, aperture, shutter, ...). They share one shape: GET
returns {"value": ..., "ability": [...]} and PUT takes {"value": ...}, so one
entity class serves both.

Which shooting settings actually exist depends on the camera's mode dial. In
A+ the body answers 503 "Mode not supported" for the exposure settings and
reports them as {"value": "", "ability": []}; those entities go unavailable
until the dial is moved to P/Tv/Av/M. The dial is physical - see the Shooting
Mode sensor, which is there to make that visible.
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
    # Shooting settings. No fallbacks: unlike the /functions/ ones above, these
    # are only meaningful in the mode the camera is currently in, and their
    # ability lists are long and body-specific. When the camera does not report
    # one, the honest answer is unavailable rather than a guessed list.
    # mdi has no ISO glyph - camera-iso does not exist and renders as a blank.
    # film is the closest true thing: ISO *is* film speed. Check any new icon
    # against the mdi set before using it.
    "iso": {"name": "ISO", "icon": "mdi:film"},
    "av": {"name": "Aperture", "icon": "mdi:camera-iris"},
    "tv": {"name": "Shutter Speed", "icon": "mdi:camera-timer"},
    "exposure": {"name": "Exposure Compensation", "icon": "mdi:plus-minus-variant"},
    "wb": {"name": "White Balance", "icon": "mdi:white-balance-auto"},
    "metering": {"name": "Metering Mode", "icon": "mdi:brightness-percent"},
    "drive": {"name": "Drive Mode", "icon": "mdi:camera-burst"},
    "flash": {"name": "Flash", "icon": "mdi:flash"},
    "afmethod": {"name": "AF Method", "icon": "mdi:focus-auto"},
    "ismode": {"name": "Image Stabilization", "icon": "mdi:camera-enhance"},
    "stillimageaspectratio": {"name": "Aspect Ratio", "icon": "mdi:aspect-ratio"},
    "picturestyle": {"name": "Picture Style", "icon": "mdi:palette"},
    "colorspace": {"name": "Color Space", "icon": "mdi:palette-swatch"},
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: CcapiCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    # Created unconditionally so the entities do not vanish when the camera is
    # merely offline; they report unavailable instead.
    entities: list[SelectEntity] = [
        CanonSettingSelect(coordinator, entry, key) for key in _SETTINGS
    ]
    entities += [
        CanonImageQualitySelect(coordinator, entry, field)
        for field in _QUALITY_FIELDS
    ]
    async_add_entities(entities)


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
            # An empty options list would make the entity useless and HA
            # rejects a current_option outside options, so a setting the
            # camera reports as "" with no ability - which is how it says
            # "not in this shooting mode" - counts as unavailable.
            and self.current_option
            and self.options
        )

    @property
    def options(self) -> list[str]:
        setting = self._setting() or {}
        ability = setting.get("ability")
        if isinstance(ability, list) and ability:
            opts = [str(a) for a in ability]
        else:
            opts = [str(o) for o in _SETTINGS[self._key].get("fallback", [])]
        # HA rejects a current_option that is not in options. Some bodies report
        # a value outside their own ability list, so keep it selectable rather
        # than letting the entity throw.
        current = self.current_option
        if current is not None and current not in opts:
            opts.append(current)
        return opts

    @property
    def current_option(self) -> str | None:
        value = (self._setting() or {}).get("value")
        # "" is how the camera says the current shooting mode does not offer
        # this setting, not a selectable option.
        return str(value) if value not in (None, "") else None

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_setting(self._key, option)


# /shooting/settings/stillimagequality is one endpoint carrying both sub-values,
# and its PUT expects both, so changing one has to resend the other.
_QUALITY_FIELDS: dict[str, dict] = {
    "jpeg": {
        "name": "JPEG Quality",
        "icon": "mdi:image",
        "fallback": [
            "none", "large_fine", "large_normal",
            "medium_fine", "medium_normal", "small",
        ],
    },
    "raw": {
        "name": "RAW Quality",
        "icon": "mdi:raw",
        "fallback": ["none", "raw", "craw"],
    },
}


class CanonImageQualitySelect(CoordinatorEntity, SelectEntity):
    """One sub-field of the stillimagequality setting."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: CcapiCoordinator, entry: ConfigEntry, field: str
    ) -> None:
        super().__init__(coordinator)
        self._field = field
        self._attr_name = _QUALITY_FIELDS[field]["name"]
        self._attr_icon = _QUALITY_FIELDS[field]["icon"]
        self._attr_unique_id = f"{entry.entry_id}_stillimagequality_{field}"
        self._attr_device_info = coordinator.build_device_info(entry)

    def _setting(self) -> dict | None:
        settings = (self.coordinator.data or {}).get(KEY_SETTINGS) or {}
        return settings.get("stillimagequality")

    def _value(self) -> str | None:
        raw = ((self._setting() or {}).get("value") or {}).get(self._field)
        # The camera reports "" for both fields when the shooting mode forbids
        # changing them, which is not a selectable option.
        return str(raw) if raw else None

    @property
    def available(self) -> bool:
        return bool(
            self.coordinator.data
            and self.coordinator.data.get(KEY_CONNECTED)
            and self._value() is not None
        )

    @property
    def options(self) -> list[str]:
        ability = ((self._setting() or {}).get("ability") or {}).get(self._field)
        if isinstance(ability, list) and ability:
            opts = [str(a) for a in ability]
        else:
            opts = list(_QUALITY_FIELDS[self._field]["fallback"])
        current = self._value()
        if current is not None and current not in opts:
            opts.append(current)
        return opts

    @property
    def current_option(self) -> str | None:
        return self._value()

    async def async_select_option(self, option: str) -> None:
        # Resend the sibling field unchanged - a PUT with only one key is
        # rejected, and omitting it would silently reset the other setting.
        value = dict((self._setting() or {}).get("value") or {})
        value[self._field] = option
        await self.coordinator.async_set_setting("stillimagequality", value)
