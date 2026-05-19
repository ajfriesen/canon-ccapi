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
    CCAPI_BASE,
    CCAPI_VER,
    CONF_HOST,
    CONF_PORT,
    DEFAULT_SAVE_PATH,
    DOMAIN,
    KEY_BEST_VER,
)
from .coordinator import CcapiCoordinator, find_endpoint_url

_LOGGER = logging.getLogger(__name__)

SERVICE_TAKE_PHOTO = "take_photo"

SERVICE_TAKE_PHOTO_SCHEMA = vol.Schema(
    {
        vol.Optional("save_path"): cv.string,
        vol.Optional("autofocus", default=True): cv.boolean,
        vol.Optional("delete_from_camera", default=False): cv.boolean,
    }
)

PLATFORMS = ["binary_sensor", "button", "camera", "sensor"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})

    host = entry.data[CONF_HOST]
    port = entry.data[CONF_PORT]

    coordinator = CcapiCoordinator(hass, host, port, entry.entry_id)
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = {"coordinator": coordinator}
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def handle_take_photo(call: ServiceCall) -> None:
        save_path = call.data.get(
            "save_path",
            os.path.join(hass.config.media_dirs.get("local", "media/local"), DEFAULT_SAVE_PATH),
        )
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


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    hass.services.async_remove(DOMAIN, SERVICE_TAKE_PHOTO)
    hass.data[DOMAIN].pop(entry.entry_id)
    return True


async def _do_take_photo(
    hass: HomeAssistant,
    host: str,
    port: int,
    save_path: str,
    autofocus: bool,
    delete_from_camera: bool,
) -> None:
    try:
        os.makedirs(save_path, exist_ok=True)
    except PermissionError:
        raise HomeAssistantError(
            f"Cannot create photo directory '{save_path}': permission denied. "
            "Pass a custom save_path to the take_photo service."
        )

    manifest_url = f"http://{host}:{port}/{CCAPI_BASE}"

    async with aiohttp.ClientSession() as session:
        # Poll GET /ccapi until 200 — activates non-AVF cameras and gives us best_ver.
        # Camera returns 503 while starting up ("Taken in preparation").
        best_ver = CCAPI_VER
        manifest = {}
        deadline = asyncio.get_event_loop().time() + 30
        while True:
            try:
                async with session.get(
                    manifest_url, timeout=aiohttp.ClientTimeout(total=8)
                ) as resp:
                    if resp.status == 200:
                        manifest = await resp.json()
                        for ver_key, endpoints in manifest.items():
                            if isinstance(endpoints, list) and ver_key > best_ver:
                                best_ver = ver_key
                        break
                    if resp.status == 503:
                        _LOGGER.debug("Camera not ready yet (503), retrying...")
                    else:
                        body = await resp.text()
                        raise HomeAssistantError(
                            f"Camera returned HTTP {resp.status}: {body}"
                        )
            except HomeAssistantError:
                raise
            except asyncio.TimeoutError:
                _LOGGER.debug("Camera poll timed out, retrying...")
            except Exception as exc:
                _LOGGER.debug("Camera poll error: %s (%s)", exc, type(exc).__name__)
            if asyncio.get_event_loop().time() >= deadline:
                raise HomeAssistantError("Camera not ready within timeout")
            await asyncio.sleep(2)

        base = f"http://{host}:{port}/{CCAPI_BASE}/{best_ver}"

        # Find the shutter endpoint from the manifest — don't assume it lives under best_ver
        shutter_url = find_endpoint_url(
            manifest, host, port, "/shooting/control/shutterbutton", method="post"
        ) or f"{base}/shooting/control/shutterbutton"
        _LOGGER.debug("Shutter URL: %s (best_ver=%s)", shutter_url, best_ver)

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

        _LOGGER.debug("Shutter triggered, waiting for image via event polling...")

        # Try event polling first — camera pushes addedcontents when image is written
        event_base_url = find_endpoint_url(manifest, host, port, "/event/polling") or f"{base}/event/polling"
        image_url = await _poll_for_new_image(session, event_base_url, host, port)

        if not image_url:
            # Fallback: sleep then walk storage tree
            _LOGGER.debug("Event polling yielded no image, falling back to storage walk")
            await asyncio.sleep(3)
            contents_base = await _discover_contents_base(session, host, port)
            image_url = await _find_latest_image(session, host, port, contents_base)

        if not image_url:
            raise HomeAssistantError("No image found after shutter trigger")

        filename = image_url.split("/")[-1].split("?")[0]
        dest = os.path.join(save_path, filename)

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


async def _poll_for_new_image(
    session: aiohttp.ClientSession, event_url: str, host: str, port: int
) -> str | None:
    """Long-poll the event endpoint for addedcontents; return image URL or None."""
    try:
        async with session.get(
            event_url,
            params={"timeout": "8"},
            timeout=aiohttp.ClientTimeout(total=12),
        ) as resp:
            if resp.status == 200:
                ev = await resp.json()
                added = ev.get("addedcontents", [])
                if added:
                    _LOGGER.debug("Event polling found %d new item(s)", len(added))
                    image_exts = {".jpg", ".jpeg", ".cr2", ".cr3", ".heif", ".heic"}
                    for item in reversed(added):
                        path = item if isinstance(item, str) else item.get("path") or item.get("url") or ""
                        if os.path.splitext(path.lower().split("?")[0])[1] in image_exts:
                            return _to_full_url(path, host, port)
    except Exception as exc:
        _LOGGER.debug("Event polling failed: %s", exc)
    return None


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
                        if not isinstance(ep, dict):
                            continue
                        raw = ep.get("path") or ep.get("url") or ""
                        if raw.startswith("http"):
                            from urllib.parse import urlparse as _up
                            raw = _up(raw).path
                        if raw.endswith("/contents") and ep.get("get"):
                            if best is None or ver > best:
                                best = ver
                            break
                if best:
                    _LOGGER.debug("Contents API version discovered: %s", best)
                    return f"http://{host}:{port}/{CCAPI_BASE}/{best}"
    except Exception as exc:
        _LOGGER.warning("Version discovery failed: %s", exc)

    for ver in ("ver110", "ver100"):
        probe = f"http://{host}:{port}/{CCAPI_BASE}/{ver}/contents"
        try:
            async with session.get(probe, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status in (200, 201):
                    _LOGGER.debug("Contents found by probe at %s", ver)
                    return f"http://{host}:{port}/{CCAPI_BASE}/{ver}"
        except Exception:
            pass

    _LOGGER.warning("Falling back to %s for contents", CCAPI_VER)
    return f"http://{host}:{port}/{CCAPI_BASE}/{CCAPI_VER}"


async def _find_latest_image(
    session: aiohttp.ClientSession, host: str, port: int, base: str
) -> str | None:
    """Walk CCAPI contents tree and return URL of the newest image."""
    data = await _get_json(session, f"{base}/contents")
    if not data:
        return None
    storage_items = _extract_items(data)
    if not storage_items:
        _LOGGER.error("No storage found on camera")
        return None
    storage_url = _to_full_url(storage_items[0], host, port)

    data = await _get_json(session, storage_url)
    if not data:
        return None
    dir_items = _extract_items(data)
    if not dir_items:
        _LOGGER.error("No directories on storage")
        return None
    dir_items_sorted = sorted(
        dir_items, key=lambda x: x.rstrip("/").split("/")[-1], reverse=True
    )
    latest_dir_url = _to_full_url(dir_items_sorted[0], host, port)

    count_data = await _get_json(session, f"{latest_dir_url}?kind=number")
    last_page = count_data.get("pagenumber", 1) if count_data else 1

    data = await _get_json(session, f"{latest_dir_url}?page={last_page}")
    if not data:
        return None
    file_items = _extract_items(data)
    if not file_items:
        _LOGGER.error("No files in directory %s (page %s)", latest_dir_url, last_page)
        return None

    image_exts = {".jpg", ".jpeg", ".cr2", ".cr3", ".heif", ".heic"}
    image_files = [
        f for f in file_items
        if os.path.splitext(f.lower().split("?")[0])[1] in image_exts
    ]
    if not image_files:
        _LOGGER.warning("No image files on last page, using all files")
        image_files = file_items

    return _to_full_url(image_files[-1], host, port)
