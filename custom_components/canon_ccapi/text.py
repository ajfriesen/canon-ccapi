"""Editable camera identity strings under /functions/registeredname/.

These get embedded in the EXIF of every shot, so they are worth setting once
per event rather than per photo.
"""

from homeassistant.components.text import TextEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, KEY_CONNECTED, KEY_SETTINGS
from .coordinator import CcapiCoordinator

# The endpoint name is also the key inside its own response body.
#
# Note "author", not "artist": the CCAPI reference calls this field artist, but
# this camera exposes /functions/registeredname/author and rejects artist.
#
# ownername is deliberately absent. Writing it returns 200 and echoes the value
# back, then takes the camera off the network a few seconds later until someone
# power-cycles it - reproduced three times on fw 1.3.0, once with the
# integration uninvolved. See docs/capabilities/canon-eos-r100-caveats.md.
# Do not add it back as a TextEntity.
_FIELDS: dict[str, dict] = {
    "copyright": {"name": "Copyright", "icon": "mdi:copyright", "max": 63},
    "author": {"name": "Author", "icon": "mdi:account-edit", "max": 63},
    "nickname": {"name": "Nickname", "icon": "mdi:tag", "max": 31},
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: CcapiCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities(
        CanonRegisteredNameText(coordinator, entry, key) for key in _FIELDS
    )


class CanonRegisteredNameText(CoordinatorEntity, TextEntity):
    _attr_has_entity_name = True
    _attr_mode = "text"

    def __init__(
        self, coordinator: CcapiCoordinator, entry: ConfigEntry, key: str
    ) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_name = _FIELDS[key]["name"]
        self._attr_icon = _FIELDS[key]["icon"]
        self._attr_native_max = _FIELDS[key]["max"]
        self._attr_native_min = 0
        self._attr_unique_id = f"{entry.entry_id}_registeredname_{key}"
        self._attr_device_info = coordinator.build_device_info(entry)

    @property
    def available(self) -> bool:
        return bool(
            self.coordinator.data
            and self.coordinator.data.get(KEY_CONNECTED)
            and self._body() is not None
        )

    def _body(self) -> dict | None:
        settings = (self.coordinator.data or {}).get(KEY_SETTINGS) or {}
        return settings.get(f"registeredname/{self._key}")

    @property
    def native_value(self) -> str | None:
        body = self._body()
        return None if body is None else str(body.get(self._key, ""))

    async def async_set_value(self, value: str) -> None:
        await self.coordinator.async_set_registered_name(self._key, value)
