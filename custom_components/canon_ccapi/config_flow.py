import asyncio
import time

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant

from .const import DOMAIN, DEFAULT_PORT, CONF_HOST, CONF_PORT, CCAPI_BASE, CCAPI_VER

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): int,
    }
)


async def _validate_connection(hass: HomeAssistant, host: str, port: int) -> None:
    async with aiohttp.ClientSession() as session:
        # Activate connection (required for non-AVF cameras).
        # Camera may return 503 while blinking red — that's expected; don't raise.
        handshake_url = f"http://{host}:{port}/{CCAPI_BASE}"
        try:
            async with session.get(
                handshake_url, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                pass
        except aiohttp.ClientConnectorError as exc:
            raise exc

        # Wait up to 30s for camera to finish startup ("Taken in preparation" 503)
        base = f"http://{host}:{port}/{CCAPI_BASE}/{CCAPI_VER}"
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            async with session.get(
                f"{base}/deviceinformation",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status == 200:
                    return
                if resp.status != 503:
                    resp.raise_for_status()
            await asyncio.sleep(2)

        raise aiohttp.ClientConnectionError("Camera not ready within 15s")


class CanonCcapiConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}

        if user_input is not None:
            try:
                await _validate_connection(
                    self.hass, user_input[CONF_HOST], user_input[CONF_PORT]
                )
            except aiohttp.ClientConnectorError:
                errors["base"] = "cannot_connect"
            except aiohttp.ClientResponseError:
                errors["base"] = "cannot_connect"
            except Exception:
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(
                    f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}"
                )
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Canon Camera ({user_input[CONF_HOST]})",
                    data=user_input,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )
