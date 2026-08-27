import logging
import re
from datetime import timedelta
from urllib.parse import urlparse

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
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
    OPT_STICKY_QUALITY,
    OPT_STICKY_SETTINGS,
)

_LOGGER = logging.getLogger(__name__)
SCAN_INTERVAL = timedelta(seconds=10)

# Writable camera settings exposed as entities, name -> endpoint suffix.
# Not all live under /functions/, so the path is explicit rather than derived.
#
# Split by how they are READ, not by what they mean: everything under
# /shooting/settings/ comes back from one aggregate GET (see
# SHOOTING_SETTINGS_PATH), while /functions/ has no such endpoint and needs a
# GET each. Writes always go to the individual endpoint either way - the
# aggregate is GET-only.
FUNCTION_SETTINGS = {
    "displayoff": "/functions/displayoff",
    "autopoweroff": "/functions/autopoweroff",
    "beep": "/functions/beep",
    # "author", not "artist" - the reference calls this field artist but this
    # camera exposes author and 404s on artist.
    "datetime": "/functions/datetime",
    "registeredname/copyright": "/functions/registeredname/copyright",
    "registeredname/author": "/functions/registeredname/author",
    "registeredname/nickname": "/functions/registeredname/nickname",
}

SHOOTING_SETTINGS = {
    "stillimagequality": "/shooting/settings/stillimagequality",
    "stillimageaspectratio": "/shooting/settings/stillimageaspectratio",
    "iso": "/shooting/settings/iso",
    "av": "/shooting/settings/av",
    "tv": "/shooting/settings/tv",
    "exposure": "/shooting/settings/exposure",
    "wb": "/shooting/settings/wb",
    "metering": "/shooting/settings/metering",
    "drive": "/shooting/settings/drive",
    "flash": "/shooting/settings/flash",
    "afmethod": "/shooting/settings/afmethod",
    "ismode": "/shooting/settings/ismode",
    "picturestyle": "/shooting/settings/picturestyle",
    "colorspace": "/shooting/settings/colorspace",
}

CAMERA_SETTINGS = {**FUNCTION_SETTINGS, **SHOOTING_SETTINGS}

# Read from the aggregate and cached for sensors, never written. The mode dial
# is physical on this body, and afoperation advertises no PUT at all.
READ_ONLY_SETTINGS = ("shootingmodedial", "afoperation")

# One GET returning every /shooting/settings/* value and ability at once. It
# answers 200 even while the individual endpoints answer 503 "Mode not
# supported" - the settings the current dial position forbids simply come back
# as {"value": "", "ability": []}.
SHOOTING_SETTINGS_PATH = "/shooting/settings"

# registeredname/ownername is absent on purpose. Only GET is safe there; a PUT
# takes the camera off the network until it is power-cycled, so the field is not
# polled and no entity offers it. See docs/capabilities/canon-eos-r100-caveats.md.

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


def merge_shooting_settings(aggregate: dict) -> dict:
    """Pick the settings we expose out of one GET /shooting/settings response.

    The aggregate also carries movie, sound-recording and per-picture-style
    sub-objects this integration has no entities for; keeping only known keys
    means an unfamiliar body cannot inflate the cache with entries nothing
    reads.
    """
    if not isinstance(aggregate, dict):
        return {}
    wanted = (*SHOOTING_SETTINGS, *READ_ONLY_SETTINGS)
    return {
        name: aggregate[name]
        for name in wanted
        if isinstance(aggregate.get(name), dict)
    }


def sticky_updates(desired: dict, current: dict) -> dict:
    """Which remembered settings need re-asserting, name -> value to PUT.

    `current` is the cached settings map as read from the camera. A setting is
    skipped when:

    - the camera does not report it at all, or reports "" / an empty dict - the
      current mode dial position forbids it, and a PUT would earn a 503;
    - it already holds the wanted value.

    stillimagequality is merged rather than replaced: that endpoint carries
    jpeg and raw together and rejects a PUT that omits either.
    """
    updates: dict = {}
    for name, want in (desired or {}).items():
        have = (current.get(name) or {}).get("value")
        if have is None or have == "" or (isinstance(have, dict) and not any(have.values())):
            continue
        if isinstance(want, dict) and isinstance(have, dict):
            merged = {**have, **want}
            if merged != have:
                updates[name] = merged
        elif have != want:
            updates[name] = want
    return updates


class CcapiCoordinator(DataUpdateCoordinator):
    def __init__(
        self, hass: HomeAssistant, host: str, port: int, entry: ConfigEntry
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"canon_ccapi_{entry.entry_id}",
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
        self._entry = entry
        # False until the first successful poll, so an HA restart counts as a
        # reconnect and re-asserts the sticky quality too.
        self._was_connected = False

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

        # A reconnect forces a settings read: the cache is up to 6 ticks old,
        # and comparing the sticky value against a stale cache would conclude
        # the camera still holds it when it has already reverted.
        reconnected = not self._was_connected
        self._was_connected = True

        refresh_settings = (
            reconnected
            or not self._last_settings
            or self._settings_tick % SETTINGS_REFRESH_EVERY == 0
        )
        self._settings_tick += 1
        if refresh_settings:
            await self._async_fetch_settings(manifest)
        if reconnected:
            await self._async_restore_sticky_settings()

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
        """Cache the writable settings and their allowed values.

        /functions/ is read one endpoint at a time; /shooting/settings/ comes
        from a single aggregate GET, which is what makes exposing a dozen
        shooting settings affordable on a 10 s poll.
        """
        settings: dict = {}
        async with aiohttp.ClientSession() as session:
            for name, suffix in FUNCTION_SETTINGS.items():
                url = find_endpoint_url(manifest, self._host, self._port, suffix)
                if not url:
                    _LOGGER.debug("%s endpoint not in manifest, skipping", name)
                    continue
                self._setting_urls[name] = url
                await self._async_fetch_one(session, name, url, settings)

            # Writes still go to the individual endpoint, so the PUT URL has to
            # be resolved even though the value arrives via the aggregate.
            for name, suffix in SHOOTING_SETTINGS.items():
                url = find_endpoint_url(
                    manifest, self._host, self._port, suffix, method="put"
                )
                if url:
                    self._setting_urls[name] = url
                else:
                    _LOGGER.debug("%s is not writable on this body", name)

            aggregate_url = find_endpoint_url(
                manifest, self._host, self._port, SHOOTING_SETTINGS_PATH
            )
            got_aggregate = False
            if aggregate_url:
                try:
                    async with session.get(
                        aggregate_url, timeout=aiohttp.ClientTimeout(total=5)
                    ) as resp:
                        if resp.status == 200:
                            settings.update(merge_shooting_settings(await resp.json()))
                            got_aggregate = True
                        else:
                            _LOGGER.debug("Shooting settings HTTP %s", resp.status)
                except Exception as exc:
                    _LOGGER.debug("Shooting settings fetch failed: %s", exc)

            # Older bodies may not advertise the aggregate. One GET each is slow
            # but correct, so fall back rather than losing the entities.
            if not got_aggregate:
                for name in SHOOTING_SETTINGS:
                    url = self._setting_urls.get(name)
                    if url:
                        await self._async_fetch_one(session, name, url, settings)

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

    async def _async_fetch_one(
        self, session: aiohttp.ClientSession, name: str, url: str, into: dict
    ) -> None:
        """GET one setting endpoint into `into`. Never raises."""
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    into[name] = await resp.json()
                else:
                    # 503 "Mode not supported" is normal here, not a fault: the
                    # mode dial decides which settings exist at all.
                    _LOGGER.debug("%s HTTP %s", name, resp.status)
        except Exception as exc:
            _LOGGER.debug("%s fetch failed: %s", name, exc)

    async def async_set_setting(self, name: str, value) -> None:
        """PUT one setting, then refresh the cached copy."""
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

        # Remember a deliberate shooting choice so it can be restored after the
        # camera loses power and silently reverts it. Only this path writes the
        # option, so a change made on the camera body is never made sticky.
        # /functions/ settings survive a power-cycle and are left out.
        if name in SHOOTING_SETTINGS:
            sticky = dict(self._sticky_settings())
            sticky[name] = dict(value) if isinstance(value, dict) else value
            self.hass.config_entries.async_update_entry(
                self._entry,
                options={**dict(self._entry.options), OPT_STICKY_SETTINGS: sticky},
            )

        # Reflect it immediately for a responsive UI, then force a real re-read
        # on the next poll rather than trusting the optimistic value.
        self._last_settings.setdefault(name, {})["value"] = value
        self._settings_tick = 0
        await self.async_request_refresh()

    def _sticky_settings(self) -> dict:
        """The remembered shooting settings, migrating the old option in place.

        The option used to hold stillimagequality alone under its own key. An
        entry created before that change still carries it, so fold it into the
        map on first read rather than silently forgetting what the user picked.
        """
        options = dict(self._entry.options or {})
        sticky = options.get(OPT_STICKY_SETTINGS)
        sticky = dict(sticky) if isinstance(sticky, dict) else {}

        legacy = options.pop(OPT_STICKY_QUALITY, None)
        if isinstance(legacy, dict) and legacy:
            sticky.setdefault("stillimagequality", dict(legacy))
            options[OPT_STICKY_SETTINGS] = sticky
            self.hass.config_entries.async_update_entry(self._entry, options=options)
            _LOGGER.debug("Migrated sticky stillimagequality into %s", OPT_STICKY_SETTINGS)

        return sticky

    async def _async_restore_sticky_settings(self) -> None:
        """Re-assert the shooting settings chosen in HA after a reconnect.

        The camera drops shooting settings on a power-cycle and restores
        whatever its own menu holds - so after any blip the booth silently
        starts shooting at a different ISO or image size, and every timeout
        tuned around the old settings is wrong. Identity settings (copyright,
        nickname) are NOT affected and need no equivalent.

        Only runs on a connect transition, never on a steady-state poll: a
        change made on the camera body while it is up is left alone until the
        next reconnect, rather than being fought every 16 seconds. Settings the
        current mode dial position forbids are skipped rather than retried -
        see sticky_updates().

        The PUTs are inline rather than via async_set_setting() because this
        runs inside _async_update_data() - async_set_setting() ends with
        async_request_refresh(), and re-entering a refresh from inside one is
        not worth the risk for calls whose results are already in hand.
        """
        updates = sticky_updates(self._sticky_settings(), self._last_settings)
        if not updates:
            return

        restored: dict = {}
        async with aiohttp.ClientSession() as session:
            for name, value in updates.items():
                url = self._setting_urls.get(name)
                if not url:
                    continue
                try:
                    async with session.put(
                        url,
                        json={"value": value},
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as resp:
                        if resp.status != 200:
                            body = await resp.text()
                            _LOGGER.warning(
                                "Restoring %s to %s failed: HTTP %s %s",
                                name,
                                value,
                                resp.status,
                                body,
                            )
                            continue
                except Exception as exc:
                    # One rejected setting must not stop the rest from being
                    # restored - they are independent endpoints.
                    _LOGGER.warning("Restoring %s to %s failed: %s", name, value, exc)
                    continue
                self._last_settings.setdefault(name, {})["value"] = value
                restored[name] = value

        if restored:
            _LOGGER.warning(
                "Camera came back with different shooting settings, restored %s - "
                "shooting settings do not survive a power-cycle",
                restored,
            )

    def _caps_url(self, suffix: str, method: str) -> str | None:
        """Resolve a full URL from the cached capability table.

        find_endpoint_url() needs the raw manifest, which is not retained -
        only the parsed path->methods map. Paths there are fully versioned
        ("/ccapi/ver130/contents"), so a plain string comparison picks the
        highest version among equal suffixes.
        """
        best = None
        for path, methods in (self._last_caps or {}).items():
            if path.endswith(suffix) and methods.get(method):
                if best is None or path > best:
                    best = path
        return f"http://{self._host}:{self._port}{best}" if best else None

    async def _contents_children(
        self, session: aiohttp.ClientSession, url: str
    ) -> list[str]:
        """Return the child paths of a node in the /contents tree."""
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise HomeAssistantError(f"GET {url} failed: HTTP {resp.status} {body}")
            data = await resp.json()
        return [
            f"http://{self._host}:{self._port}{p}"
            for p in data.get("path", [])
            if isinstance(p, str)
        ]

    async def async_delete_all_contents(self) -> dict:
        """Delete every image on every card by removing each DCIM directory.

        DELETE is only accepted on directory nodes. Both /contents and
        /contents/sd answer 405 "Method not allowed" even though the manifest
        advertises delete on /contents - so walk down to the 1xxCANON level
        and delete there. One request per directory, not per file.
        """
        root = self._caps_url("/contents", "get")
        if not root:
            raise HomeAssistantError("Camera does not expose /contents")

        deleted_files = 0
        directories: list[str] = []
        try:
            async with aiohttp.ClientSession() as session:
                for storage in await self._contents_children(session, root):
                    for directory in await self._contents_children(session, storage):
                        # Count before deleting, purely so the caller can be
                        # told what was destroyed. A failure here must not
                        # block the delete.
                        try:
                            async with session.get(
                                f"{directory}?kind=number",
                                timeout=aiohttp.ClientTimeout(total=15),
                            ) as resp:
                                if resp.status == 200:
                                    body = await resp.json()
                                    deleted_files += int(body.get("contentsnumber", 0))
                        except Exception as exc:
                            _LOGGER.debug("Counting %s failed: %s", directory, exc)

                        async with session.delete(
                            directory, timeout=aiohttp.ClientTimeout(total=120)
                        ) as resp:
                            if resp.status != 200:
                                body = await resp.text()
                                raise HomeAssistantError(
                                    f"Deleting {directory} failed: "
                                    f"HTTP {resp.status} {body}"
                                )
                        directories.append(directory.rsplit("/", 1)[-1])
        except HomeAssistantError:
            raise
        except Exception as exc:
            raise HomeAssistantError(f"Deleting all images failed: {exc}") from exc

        _LOGGER.info(
            "Deleted %s file(s) from %s directory(ies): %s",
            deleted_files,
            len(directories),
            ", ".join(directories) or "none",
        )
        await self.async_request_refresh()
        return {"deleted": deleted_files, "directories": directories}

    async def async_format_card(self, name: str | None = None) -> dict:
        """Format a card.

        POST /functions/cardformat with {"name": "<storage name>"}. The
        reference's "formattype" is accepted but NOT validated on the R100 -
        an unknown value still returns 200 - so it is not sent, rather than
        implying a choice the camera does not honour.
        """
        url = self._caps_url("/functions/cardformat", "post")
        if not url:
            raise HomeAssistantError("Camera does not expose /functions/cardformat")

        storages = [
            s.get("name")
            for s in (self.data or {}).get(KEY_STORAGE, [])
            if isinstance(s, dict) and s.get("name")
        ]
        if name is None:
            if not storages:
                raise HomeAssistantError(
                    "No card reported by the camera - cannot pick one to format"
                )
            name = storages[0]
        elif storages and name not in storages:
            raise ServiceValidationError(
                f"Camera reports no card named {name!r} (has: {', '.join(storages)})"
            )

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json={"name": name},
                    timeout=aiohttp.ClientTimeout(total=180),
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        raise HomeAssistantError(
                            f"Formatting {name} failed: HTTP {resp.status} {body}"
                        )
        except HomeAssistantError:
            raise
        except Exception as exc:
            raise HomeAssistantError(f"Formatting {name} failed: {exc}") from exc

        _LOGGER.info("Formatted card %s", name)
        await self.async_request_refresh()
        return {"formatted": name}

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
        self._was_connected = False
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
