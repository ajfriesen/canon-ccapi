import logging
from datetime import timedelta
from urllib.parse import urlparse

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CCAPI_BASE,
    CCAPI_VER,
    CONF_HOST,
    CONF_PORT,
    DOMAIN,
    KEY_BATTERY,
    KEY_BEST_VER,
    KEY_CAPS,
    KEY_CONNECTED,
    KEY_DEVICE_INFO,
    KEY_SETTINGS,
    KEY_STORAGE,
    KEY_TEMPERATURE,
)

_LOGGER = logging.getLogger(__name__)
SCAN_INTERVAL = timedelta(seconds=10)

# Writable camera settings under /functions/, exposed as select entities.
CAMERA_SETTINGS = ("displayoff", "autopoweroff", "beep")

# These only change when somebody changes them, so they do not justify a round
# trip on every 10 s poll: that would add three more serial 5 s-timeout GETs to
# a cycle the shooting preflight depends on being quick. Refresh on the first
# update, then roughly once a minute.
SETTINGS_REFRESH_EVERY = 6


def _endpoint_path(ep: dict) -> str:
    """Normalize endpoint path from both ver1.0.0 (full URL) and ver1.1.0+ (path) formats."""
    raw = ep.get("path") or ep.get("url") or ""
    if raw.startswith("http"):
        raw = urlparse(raw).path
    return raw


def find_endpoint_url(
    manifest: dict, host: str, port: int, suffix: str, method: str = "get"
) -> str | None:
    """Return full URL for the highest-version endpoint whose path ends with suffix."""
    best_ver = None
    best_path = None
    for ver_key, endpoints in manifest.items():
        if not isinstance(endpoints, list):
            continue
        for ep in endpoints:
            if not isinstance(ep, dict):
                continue
            path = _endpoint_path(ep)
            if path.endswith(suffix) and ep.get(method):
                if best_ver is None or ver_key > best_ver:
                    best_ver = ver_key
                    best_path = path
    if best_path:
        raw = best_path
        if raw.startswith("http"):
            return raw
        return f"http://{host}:{port}{raw}"
    return None


class CcapiCoordinator(DataUpdateCoordinator):
    def __init__(
        self, hass: HomeAssistant, host: str, port: int, entry_id: str
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"canon_ccapi_{entry_id}",
            update_interval=SCAN_INTERVAL,
        )
        self._host = host
        self._port = port
        self._last_caps: dict = {}
        self._last_best_ver: str = CCAPI_VER
        self._last_device_info: dict = {}
        self._last_settings: dict = {}
        self._setting_urls: dict = {}
        self._settings_tick = 0

    async def _async_update_data(self) -> dict:
        manifest_url = f"http://{self._host}:{self._port}/{CCAPI_BASE}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    manifest_url, timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    if resp.status != 200:
                        _LOGGER.debug("Camera poll: HTTP %s", resp.status)
                        return self._disconnected()
                    manifest = await resp.json()
        except Exception as exc:
            _LOGGER.debug("Camera poll failed: %s", exc)
            return self._disconnected()

        caps: dict[str, dict] = {}
        best_ver = CCAPI_VER
        for ver_key, endpoints in manifest.items():
            if not isinstance(endpoints, list):
                continue
            if ver_key > best_ver:
                best_ver = ver_key
            for ep in endpoints:
                if not isinstance(ep, dict):
                    continue
                path = _endpoint_path(ep)
                if path:
                    caps[path] = {
                        "get": bool(ep.get("get")),
                        "post": bool(ep.get("post")),
                        "put": bool(ep.get("put")),
                        "delete": bool(ep.get("delete")),
                    }

        self._last_caps = caps
        self._last_best_ver = best_ver

        if not self._last_device_info:
            device_info_url = find_endpoint_url(manifest, self._host, self._port, "/deviceinformation")
            if device_info_url:
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(device_info_url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                            if resp.status == 200:
                                self._last_device_info = await resp.json()
                except Exception as exc:
                    _LOGGER.debug("Device info fetch failed: %s", exc)

        battery_url = find_endpoint_url(manifest, self._host, self._port, "/devicestatus/battery")
        storage_url = find_endpoint_url(manifest, self._host, self._port, "/devicestatus/storage")
        temperature_url = find_endpoint_url(manifest, self._host, self._port, "/devicestatus/temperature")

        _LOGGER.debug("Battery URL from manifest: %s", battery_url)
        _LOGGER.debug("Storage URL from manifest: %s", storage_url)
        _LOGGER.debug("Temperature URL from manifest: %s", temperature_url)

        battery = None
        storage = []
        temperature = None

        async with aiohttp.ClientSession() as session:
            if battery_url:
                try:
                    async with session.get(
                        battery_url, timeout=aiohttp.ClientTimeout(total=5)
                    ) as resp:
                        if resp.status == 200:
                            battery = await resp.json()
                        else:
                            _LOGGER.debug("Battery HTTP %s", resp.status)
                except Exception as exc:
                    _LOGGER.debug("Battery fetch failed: %s", exc)
            else:
                _LOGGER.debug("Battery endpoint not in manifest, skipping")

            if storage_url:
                try:
                    async with session.get(
                        storage_url, timeout=aiohttp.ClientTimeout(total=5)
                    ) as resp:
                        if resp.status == 200:
                            sdata = await resp.json()
                            storage = sdata.get("storagelist", [])
                        else:
                            _LOGGER.debug("Storage HTTP %s", resp.status)
                except Exception as exc:
                    _LOGGER.debug("Storage fetch failed: %s", exc)
            else:
                _LOGGER.debug("Storage endpoint not in manifest, skipping")

            if temperature_url:
                try:
                    async with session.get(
                        temperature_url, timeout=aiohttp.ClientTimeout(total=5)
                    ) as resp:
                        if resp.status == 200:
                            temperature = await resp.json()
                        else:
                            _LOGGER.debug("Temperature HTTP %s", resp.status)
                except Exception as exc:
                    _LOGGER.debug("Temperature fetch failed: %s", exc)
            else:
                _LOGGER.debug("Temperature endpoint not in manifest, skipping")

        refresh_settings = (
            not self._last_settings
            or self._settings_tick % SETTINGS_REFRESH_EVERY == 0
        )
        self._settings_tick += 1
        if refresh_settings:
            await self._async_fetch_settings(manifest)

        return {
            KEY_CONNECTED: True,
            KEY_BEST_VER: best_ver,
            KEY_CAPS: caps,
            KEY_BATTERY: battery,
            KEY_STORAGE: storage,
            KEY_TEMPERATURE: temperature,
            KEY_SETTINGS: self._last_settings,
            KEY_DEVICE_INFO: self._last_device_info,
        }

    async def _async_fetch_settings(self, manifest: dict) -> None:
        """Cache the writable /functions/ settings and their allowed values."""
        settings: dict = {}
        async with aiohttp.ClientSession() as session:
            for name in CAMERA_SETTINGS:
                url = find_endpoint_url(
                    manifest, self._host, self._port, f"/functions/{name}"
                )
                if not url:
                    _LOGGER.debug("%s endpoint not in manifest, skipping", name)
                    continue
                self._setting_urls[name] = url
                try:
                    async with session.get(
                        url, timeout=aiohttp.ClientTimeout(total=5)
                    ) as resp:
                        if resp.status == 200:
                            settings[name] = await resp.json()
                        else:
                            _LOGGER.debug("%s HTTP %s", name, resp.status)
                except Exception as exc:
                    _LOGGER.debug("%s fetch failed: %s", name, exc)
        # Only replace the cache on a successful read, so one transient failure
        # does not blank out the select entities.
        if settings:
            self._last_settings = settings

    async def async_set_setting(self, name: str, value: str) -> None:
        """PUT one /functions/ setting, then refresh the cached copy."""
        url = self._setting_urls.get(name)
        if not url:
            raise HomeAssistantError(f"Camera does not expose /functions/{name}")
        try:
            async with aiohttp.ClientSession() as session:
                async with session.put(
                    url,
                    json={"value": value},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        raise HomeAssistantError(
                            f"Setting {name}={value} failed: HTTP {resp.status} {body}"
                        )
        except HomeAssistantError:
            raise
        except Exception as exc:
            raise HomeAssistantError(f"Setting {name}={value} failed: {exc}") from exc

        # Reflect it immediately for a responsive UI, then force a real re-read
        # on the next poll rather than trusting the optimistic value.
        self._last_settings.setdefault(name, {})["value"] = value
        self._settings_tick = 0
        await self.async_request_refresh()

    def build_device_info(self, entry) -> dict:
        info = self._last_device_info
        d = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": info.get("productname") or f"Canon Camera ({entry.data[CONF_HOST]}:{entry.data[CONF_PORT]})",
            "manufacturer": info.get("manufacturer") or "Canon",
            "configuration_url": f"http://{entry.data[CONF_HOST]}:{entry.data[CONF_PORT]}/ccapi",
        }
        if info.get("productname"):
            d["model"] = info["productname"]
        if info.get("firmwareversion"):
            d["sw_version"] = info["firmwareversion"]
        if info.get("serialnumber"):
            d["serial_number"] = info["serialnumber"]
        if info.get("macaddress"):
            d["connections"] = {("mac", info["macaddress"])}
        return d

    def _disconnected(self) -> dict:
        return {
            KEY_CONNECTED: False,
            KEY_BEST_VER: self._last_best_ver,
            KEY_CAPS: self._last_caps,
            KEY_BATTERY: None,
            KEY_STORAGE: [],
            KEY_TEMPERATURE: None,
            KEY_SETTINGS: self._last_settings,
            KEY_DEVICE_INFO: self._last_device_info,
        }
