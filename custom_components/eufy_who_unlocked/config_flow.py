"""Config flow for the eufy Who Unlocked integration.

Asks ONLY for the bridge URL. There are no eufy credentials in Home
Assistant — the bridge (running on the user's Mac Mini) holds the
eufy session, so there is nothing to re-authenticate here and no
reauth step exists.
"""
import logging
from typing import Any
from urllib.parse import urlparse

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    BRIDGE_TIMEOUT_S,
    CONF_BRIDGE_URL,
    DEFAULT_BRIDGE_URL,
    DOMAIN,
    ENDPOINT_HEALTH,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_BRIDGE_URL, default=DEFAULT_BRIDGE_URL): str,
    }
)


def _normalise_bridge_url(url: str) -> str:
    """Normalise a bridge URL for use as the config entry unique_id.

    Lowercases scheme/host and strips any trailing slash, so
    "HTTP://Mac-Mini.local:8787/" and "http://mac-mini.local:8787"
    count as the same bridge.
    """
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme.lower()}://{host}{port}"


def _is_valid_bridge_url(url: str) -> bool:
    """Check the URL is a well-formed http(s) URL with a host."""
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.hostname)


class EufyWhoUnlockedConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for eufy Who Unlocked."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial bridge-URL step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            bridge_url = user_input[CONF_BRIDGE_URL].strip()

            if not _is_valid_bridge_url(bridge_url):
                errors["base"] = "invalid_url"
            else:
                # Validate by asking the bridge for /health: it must
                # answer 200 with a JSON body. Anything else (refused
                # connection, timeout, non-200, non-JSON) is
                # "cannot_connect" — the bridge is not reachable there.
                session = async_get_clientsession(self.hass)
                try:
                    async with session.get(
                        f"{bridge_url.rstrip('/')}{ENDPOINT_HEALTH}",
                        timeout=aiohttp.ClientTimeout(total=BRIDGE_TIMEOUT_S),
                    ) as response:
                        if response.status != 200:
                            errors["base"] = "cannot_connect"
                        else:
                            health = await response.json()
                            if not isinstance(health, dict) or "status" not in health:
                                errors["base"] = "cannot_connect"
                except (aiohttp.ClientError, TimeoutError) as err:
                    _LOGGER.debug("Bridge health check failed for %s: %s", bridge_url, err)
                    errors["base"] = "cannot_connect"
                except Exception as err:  # noqa: BLE001 - surface as a form error
                    _LOGGER.exception("Unexpected error checking bridge health: %s", err)
                    errors["base"] = "unknown"

            if not errors:
                await self.async_set_unique_id(_normalise_bridge_url(bridge_url))
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title="eufy Who Unlocked",
                    data={CONF_BRIDGE_URL: bridge_url.rstrip("/")},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )
