import asyncio
import logging
import os

import aiohttp
import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
import homeassistant.helpers.config_validation as cv

from .const import (
    DOMAIN,
    CONF_HOST,
    CONF_PORT,
    CCAPI_BASE,
    CCAPI_VER,
    DEFAULT_SAVE_PATH,
)

_LOGGER = logging.getLogger(__name__)

SERVICE_TAKE_PHOTO = "take_photo"

SERVICE_TAKE_PHOTO_SCHEMA = vol.Schema(
    {
        vol.Optional("save_path"): cv.string,
        vol.Optional("autofocus", default=True): cv.boolean,
        vol.Optional("delete_from_camera", default=False): cv.boolean,
    }
)


KEEPALIVE_INTERVAL = 10  # seconds


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})
    stop_event = asyncio.Event()
    hass.data[DOMAIN][entry.entry_id] = {"stop_event": stop_event}
    hass.async_create_background_task(
        _keepalive_loop(entry.data[CONF_HOST], entry.data[CONF_PORT], stop_event),
        name=f"canon_ccapi_keepalive_{entry.entry_id}",
    )

    async def handle_take_photo(call: ServiceCall) -> None:
        host = entry.data[CONF_HOST]
        port = entry.data[CONF_PORT]
        save_path = call.data.get("save_path", hass.config.path(DEFAULT_SAVE_PATH))
        autofocus = call.data.get("autofocus", True)
        delete_from_camera = call.data.get("delete_from_camera", False)

        try:
            await _do_take_photo(
                hass, host, port, save_path, autofocus, delete_from_camera
            )
        except HomeAssistantError:
            raise
        except Exception as exc:
            _LOGGER.exception("take_photo failed unexpectedly")
            raise HomeAssistantError(str(exc)) from exc

    hass.services.async_register(
        DOMAIN,
        SERVICE_TAKE_PHOTO,
        handle_take_photo,
        schema=SERVICE_TAKE_PHOTO_SCHEMA,
    )

    return True


async def _do_take_photo(
    hass: HomeAssistant,
    host: str,
    port: int,
    save_path: str,
    autofocus: bool,
    delete_from_camera: bool,
) -> None:
    os.makedirs(save_path, exist_ok=True)

    base = f"http://{host}:{port}/{CCAPI_BASE}/{CCAPI_VER}"

    async with aiohttp.ClientSession() as session:
        # Handshake: GET /ccapi activates the connection on non-AVF cameras
        handshake_url = f"http://{host}:{port}/{CCAPI_BASE}"
        try:
            async with session.get(
                handshake_url, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status not in (200, 201):
                    _LOGGER.warning(
                        "Handshake returned HTTP %s, continuing", resp.status
                    )
        except Exception as exc:
            raise HomeAssistantError(f"Cannot reach camera: {exc}") from exc

        # Wait for camera to finish startup ("Taken in preparation" 503 phase)
        if not await _wait_until_ready(session, base):
            raise HomeAssistantError("Camera not ready within timeout")

        # Trigger shutter
        shutter_url = f"{base}/shooting/control/shutterbutton"
        async with session.post(
            shutter_url,
            json={"af": autofocus},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status not in (200, 202):
                body = await resp.text()
                raise HomeAssistantError(
                    f"Shutter failed: HTTP {resp.status} — {body}"
                )

        _LOGGER.debug("Shutter triggered, waiting for image write...")
        await asyncio.sleep(3)

        # Find newest image on camera storage
        contents_base = await _discover_contents_base(session, host, port)
        image_url = await _find_latest_image(session, host, port, contents_base)
        if not image_url:
            raise HomeAssistantError("No image found after shutter trigger")

        filename = image_url.split("/")[-1].split("?")[0]
        dest = os.path.join(save_path, filename)

        # Download — strip any query params from the contents URL, use bare path
        download_url = image_url.split("?")[0]
        async with session.get(
            download_url, timeout=aiohttp.ClientTimeout(total=60)
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise HomeAssistantError(
                    f"Image download failed: HTTP {resp.status} — {body}"
                )
            content = await resp.read()

        with open(dest, "wb") as f:
            f.write(content)

        _LOGGER.info("Photo saved: %s", dest)

        if delete_from_camera:
            delete_url = image_url.split("?")[0]
            async with session.delete(
                delete_url, timeout=aiohttp.ClientTimeout(total=10)
            ) as del_resp:
                if del_resp.status in (200, 204):
                    _LOGGER.info("Deleted from camera: %s", delete_url)
                else:
                    body = await del_resp.text()
                    _LOGGER.warning(
                        "Delete from camera failed: HTTP %s — %s",
                        del_resp.status,
                        body,
                    )

        hass.bus.async_fire(
            f"{DOMAIN}_photo_taken",
            {"path": dest, "filename": filename},
        )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.services.async_remove(DOMAIN, SERVICE_TAKE_PHOTO)
    hass.data[DOMAIN].pop(entry.entry_id)["stop_event"].set()
    return True


async def _keepalive_loop(host: str, port: int, stop_event: asyncio.Event) -> None:
    """Periodically hit GET /ccapi so camera stays activated after power-on."""
    url = f"http://{host}:{port}/{CCAPI_BASE}"
    async with aiohttp.ClientSession() as session:
        while not stop_event.is_set():
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    _LOGGER.debug("Keepalive %s: HTTP %s", url, resp.status)
            except Exception as exc:
                _LOGGER.debug("Keepalive failed (camera off?): %s", exc)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=KEEPALIVE_INTERVAL)
            except asyncio.TimeoutError:
                pass


async def _discover_contents_base(
    session: aiohttp.ClientSession, host: str, port: int
) -> str:
    """Return base URL for the highest CCAPI version that exposes GET /contents."""
    url = f"http://{host}:{port}/{CCAPI_BASE}"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                data = await resp.json()
                best = None
                for ver, endpoints in data.items():
                    if not isinstance(endpoints, list):
                        continue
                    for ep in endpoints:
                        if (
                            isinstance(ep, dict)
                            and ep.get("get")
                            and ep.get("path", "").endswith("/contents")
                        ):
                            if best is None or ver > best:
                                best = ver
                            break
                if best:
                    _LOGGER.debug("Contents API version discovered: %s", best)
                    return f"http://{host}:{port}/{CCAPI_BASE}/{best}"
    except Exception as exc:
        _LOGGER.warning("Version discovery failed: %s", exc)
    _LOGGER.warning("Falling back to ver100 for contents")
    return f"http://{host}:{port}/{CCAPI_BASE}/{CCAPI_VER}"


async def _wait_until_ready(
    session: aiohttp.ClientSession, base: str, timeout: int = 30
) -> bool:
    """Poll until camera exits 'Taken in preparation' (503) startup state."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        try:
            async with session.get(
                f"{base}/deviceinformation",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status == 200:
                    return True
                if resp.status == 503:
                    _LOGGER.debug("Camera not ready yet (503), retrying...")
                    await asyncio.sleep(2)
                else:
                    _LOGGER.warning(
                        "Unexpected status %s from deviceinformation", resp.status
                    )
                    return False
        except aiohttp.ClientConnectorError:
            await asyncio.sleep(2)
    return False


def _extract_items(data: dict) -> list[str]:
    """Parse both ver1.0.0 {"url":[...]} and ver1.1.0+ {"path":[...]} formats."""
    return data.get("path") or data.get("url") or []


def _to_full_url(path_or_url: str, host: str, port: int) -> str:
    if path_or_url.startswith("http"):
        return path_or_url
    return f"http://{host}:{port}{path_or_url}"


async def _get_json(session: aiohttp.ClientSession, url: str) -> dict | None:
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                return await resp.json()
            _LOGGER.warning("GET %s returned HTTP %s", url, resp.status)
            return None
    except Exception as exc:
        _LOGGER.error("GET %s failed: %s", url, exc)
        return None


async def _find_latest_image(
    session: aiohttp.ClientSession, host: str, port: int, base: str
) -> str | None:
    """Walk CCAPI contents tree and return URL of the newest image."""

    # 1. Storage list — returns {"url":[...]} (v1.0) or {"path":[...]} (v1.1+)
    #    e.g. ["http://.../contents/sd"] or ["/ccapi/ver110/contents/card1"]
    data = await _get_json(session, f"{base}/contents")
    if not data:
        return None
    storage_items = _extract_items(data)
    if not storage_items:
        _LOGGER.error("No storage found on camera")
        return None
    storage_url = _to_full_url(storage_items[0], host, port)

    # 2. Directory list — returns full URLs or paths of DCIM sub-directories
    #    e.g. ["http://.../contents/sd/100CANON", ".../101CANON"]
    data = await _get_json(session, storage_url)
    if not data:
        return None
    dir_items = _extract_items(data)
    if not dir_items:
        _LOGGER.error("No directories on storage")
        return None
    # Sort by directory name; highest = most recently created
    dir_items_sorted = sorted(dir_items, key=lambda x: x.rstrip("/").split("/")[-1], reverse=True)
    latest_dir_url = _to_full_url(dir_items_sorted[0], host, port)

    # 3. Get page count so we can fetch the last page (newest files are last in asc order)
    count_data = await _get_json(session, f"{latest_dir_url}?kind=number")
    last_page = count_data.get("pagenumber", 1) if count_data else 1

    # 4. Fetch last page of file list
    data = await _get_json(session, f"{latest_dir_url}?page={last_page}")
    if not data:
        return None
    file_items = _extract_items(data)
    if not file_items:
        _LOGGER.error("No files in directory %s (page %s)", latest_dir_url, last_page)
        return None

    # Filter to still images; last item in ascending list = newest
    image_exts = {".jpg", ".jpeg", ".cr2", ".cr3", ".heif", ".heic"}
    image_files = [
        f for f in file_items
        if os.path.splitext(f.lower().split("?")[0])[1] in image_exts
    ]
    if not image_files:
        _LOGGER.warning("No image files on last page, using all files")
        image_files = file_items

    return _to_full_url(image_files[-1], host, port)
