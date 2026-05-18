import logging
from datetime import timedelta
from urllib.parse import urlparse

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CCAPI_BASE,
    CCAPI_VER,
    KEY_BATTERY,
    KEY_BEST_VER,
    KEY_CAPS,
    KEY_CONNECTED,
    KEY_STORAGE,
)

_LOGGER = logging.getLogger(__name__)
SCAN_INTERVAL = timedelta(seconds=10)


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

        battery_url = find_endpoint_url(manifest, self._host, self._port, "/devicestatus/battery")
        storage_url = find_endpoint_url(manifest, self._host, self._port, "/devicestatus/storage")

        _LOGGER.debug("Battery URL from manifest: %s", battery_url)
        _LOGGER.debug("Storage URL from manifest: %s", storage_url)

        battery = None
        storage = []

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

        return {
            KEY_CONNECTED: True,
            KEY_BEST_VER: best_ver,
            KEY_CAPS: caps,
            KEY_BATTERY: battery,
            KEY_STORAGE: storage,
        }

    def _disconnected(self) -> dict:
        return {
            KEY_CONNECTED: False,
            KEY_BEST_VER: self._last_best_ver,
            KEY_CAPS: self._last_caps,
            KEY_BATTERY: None,
            KEY_STORAGE: [],
        }
