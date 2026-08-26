import logging
import re
from datetime import timedelta
from urllib.parse import urlparse

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

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
    KEY_LENS,
    KEY_RECORDABLE,
    KEY_SETTINGS,
    KEY_STORAGE,
    KEY_TEMPERATURE,
)

_LOGGER = logging.getLogger(__name__)
SCAN_INTERVAL = timedelta(seconds=10)

# Writable camera settings exposed as select entities, name -> endpoint suffix.
# Not all live under /functions/, so the path is explicit rather than derived.
CAMERA_SETTINGS = {
    "displayoff": "/functions/displayoff",
    "autopoweroff": "/functions/autopoweroff",
    "beep": "/functions/beep",
    "stillimagequality": "/shooting/settings/stillimagequality",
    # "author", not "artist" - the reference calls this field artist but this
    # camera exposes author and 404s on artist.
    "datetime": "/functions/datetime",
    "registeredname/copyright": "/functions/registeredname/copyright",
    "registeredname/author": "/functions/registeredname/author",
    "registeredname/ownername": "/functions/registeredname/ownername",
    "registeredname/nickname": "/functions/registeredname/nickname",
}

# These only change when somebody changes them, so they do not justify a round
# trip on every 10 s poll: that would add several more serial 5 s-timeout GETs to
# a cycle the shooting preflight depends on being quick. Refresh on the first
# update, then roughly once a minute.
SETTINGS_REFRESH_EVERY = 6

# Built by hand rather than strftime: %a/%b follow the process locale, and the
# camera rejects a localised day name with "Invalid parameter".
_RFC1123_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_RFC1123_MONTHS = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)


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
        self._liveview_active = False
        self._last_lens: dict = {}

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
        recordable_url = find_endpoint_url(manifest, self._host, self._port, "/shooting/information/recordable")

        _LOGGER.debug("Battery URL from manifest: %s", battery_url)
        _LOGGER.debug("Storage URL from manifest: %s", storage_url)
        _LOGGER.debug("Temperature URL from manifest: %s", temperature_url)

        battery = None
        storage = []
        temperature = None
        recordable = None

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

            # One extra GET per poll, justified for a camera left shooting
            # unattended: "shots remaining" is the number that says when the
            # card is about to run out.
            if recordable_url:
                try:
                    async with session.get(
                        recordable_url, timeout=aiohttp.ClientTimeout(total=5)
                    ) as resp:
                        if resp.status == 200:
                            recordable = await resp.json()
                        else:
                            _LOGGER.debug("Recordable HTTP %s", resp.status)
                except Exception as exc:
                    _LOGGER.debug("Recordable fetch failed: %s", exc)
            else:
                _LOGGER.debug("Recordable endpoint not in manifest, skipping")

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
            KEY_RECORDABLE: recordable,
            KEY_LENS: self._last_lens,
            KEY_SETTINGS: self._last_settings,
            KEY_DEVICE_INFO: self._last_device_info,
        }

    @property
    def liveview_active(self) -> bool:
        """Whether we last succeeded in starting live view.

        CCAPI exposes no way to query this, so it is tracked rather than read.
        """
        return self._liveview_active

    def liveview_url(self) -> str:
        caps = (self.data or {}).get(KEY_CAPS, {})
        path = next(
            (p for p, m in caps.items() if p.endswith("/shooting/liveview") and m.get("post")),
            None,
        )
        if path:
            return f"http://{self._host}:{self._port}{path}"
        best_ver = (self.data or {}).get(KEY_BEST_VER, CCAPI_VER)
        return f"http://{self._host}:{self._port}/{CCAPI_BASE}/{best_ver}/shooting/liveview"

    async def async_set_liveview(self, on: bool, size: str = "small") -> bool:
        """Start or stop live view. Returns True on success.

        BOTH keys are mandatory. Sending liveviewsize alone is rejected with
        "Invalid parameter" whatever its value - which is why the old
        {"liveviewsize": "off"} never actually stopped anything, in take_photo
        or anywhere else.

        Stopping additionally requires cameradisplay "on": the camera refuses a
        state where neither a client stream nor its own screen is showing the
        scene, and answers 400 to off+off. The rear screen therefore comes back
        on when live view stops; the displayoff timer blanks it again shortly.

        Starting uses cameradisplay "off" so the preview streams to Home
        Assistant without lighting up the camera's screen.

        Verified sizes on an EOS R100: small and medium. large is rejected.
        """
        body = (
            {"liveviewsize": size, "cameradisplay": "off"}
            if on
            else {"liveviewsize": "off", "cameradisplay": "on"}
        )
        url = self.liveview_url()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, json=body, timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    text = await resp.text()
                    if resp.status in (200, 201):
                        self._liveview_active = on
                        _LOGGER.debug("Live view %s", "started" if on else "stopped")
                        return True
                    _LOGGER.warning(
                        "Live view %s failed: HTTP %s - %s",
                        "start" if on else "stop",
                        resp.status,
                        text[:200],
                    )
        except Exception as exc:  # noqa: BLE001 - reported to the caller
            _LOGGER.warning("Live view %s failed: %s", "start" if on else "stop", exc)
        return False

    async def async_set_registered_name(self, field: str, value: str) -> None:
        """PUT one /functions/registeredname/ field.

        These do not use the {"value": ...} envelope the other settings use -
        the body key is the field name itself, e.g. {"copyright": "..."}.
        """
        name = f"registeredname/{field}"
        url = self._setting_urls.get(name)
        if not url:
            raise HomeAssistantError(
                f"Camera does not expose /functions/registeredname/{field}"
            )
        try:
            async with aiohttp.ClientSession() as session:
                async with session.put(
                    url,
                    json={field: value},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        raise HomeAssistantError(
                            f"Setting {field} failed: HTTP {resp.status} {body}"
                        )
        except HomeAssistantError:
            raise
        except Exception as exc:
            raise HomeAssistantError(f"Setting {field} failed: {exc}") from exc

        self._last_settings.setdefault(name, {})[field] = value
        self._settings_tick = 0
        await self.async_request_refresh()

    async def async_sync_datetime(self) -> str:
        """Set the camera's clock from Home Assistant's local time.

        Correct only the wall clock, and echo back whatever offset and dst flag
        the camera already reports. Verified on an EOS R100: the camera stores
        the string verbatim and does not recompute the displayed time from the
        offset, so preserving those two fields cannot shift the clock by an
        hour if Canon's offset convention differs from ours. It reports +0100
        with dst true for a Berlin summer, i.e. offset means the standard zone.

        Day and month names must be English. A localised string is rejected
        with "Invalid parameter", so they are built from fixed tuples rather
        than strftime, whose %a/%b follow the process locale.
        """
        url = self._setting_urls.get("datetime")
        if not url:
            raise HomeAssistantError("Camera does not expose /functions/datetime")

        current = (self._last_settings.get("datetime") or {}).get("datetime", "")
        offset = current.rsplit(" ", 1)[-1] if current else ""
        if not re.fullmatch(r"[+-]\d{4}", offset):
            raise HomeAssistantError(
                f"Cannot read the camera's UTC offset (got {current!r}); "
                "refusing to guess it"
            )
        dst = bool((self._last_settings.get("datetime") or {}).get("dst", False))

        now = dt_util.now()
        stamp = (
            f"{_RFC1123_DAYS[now.weekday()]}, {now.day:02d} "
            f"{_RFC1123_MONTHS[now.month - 1]} {now.year} "
            f"{now.hour:02d}:{now.minute:02d}:{now.second:02d} {offset}"
        )

        try:
            async with aiohttp.ClientSession() as session:
                async with session.put(
                    url,
                    json={"datetime": stamp, "dst": dst},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        raise HomeAssistantError(
                            f"Clock sync failed: HTTP {resp.status} {body}"
                        )
        except HomeAssistantError:
            raise
        except Exception as exc:
            raise HomeAssistantError(f"Clock sync failed: {exc}") from exc

        _LOGGER.info("Camera clock synced to %s (dst=%s)", stamp, dst)
        self._last_settings.setdefault("datetime", {})["datetime"] = stamp
        self._settings_tick = 0
        await self.async_request_refresh()
        return stamp

    def note_liveview_stopped(self) -> None:
        """Record that something else stopped live view, e.g. taking a photo."""
        self._liveview_active = False

    async def _async_fetch_settings(self, manifest: dict) -> None:
        """Cache the writable /functions/ settings and their allowed values."""
        settings: dict = {}
        async with aiohttp.ClientSession() as session:
            for name, suffix in CAMERA_SETTINGS.items():
                url = find_endpoint_url(manifest, self._host, self._port, suffix)
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

        # Lens rides the same slow cadence: it only changes when glass is
        # swapped, so it does not belong in the 10 s poll.
        lens_url = find_endpoint_url(
            manifest, self._host, self._port, "/devicestatus/lens"
        )
        if lens_url:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        lens_url, timeout=aiohttp.ClientTimeout(total=5)
                    ) as resp:
                        if resp.status == 200:
                            self._last_lens = await resp.json()
                        else:
                            _LOGGER.debug("Lens HTTP %s", resp.status)
            except Exception as exc:
                _LOGGER.debug("Lens fetch failed: %s", exc)

    async def async_set_setting(self, name: str, value) -> None:
        """PUT one /functions/ setting, then refresh the cached copy."""
        url = self._setting_urls.get(name)
        if not url:
            raise HomeAssistantError(
                f"Camera does not expose {CAMERA_SETTINGS.get(name, name)}"
            )
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
            KEY_RECORDABLE: None,
            KEY_LENS: self._last_lens,
            KEY_SETTINGS: self._last_settings,
            KEY_DEVICE_INFO: self._last_device_info,
        }
