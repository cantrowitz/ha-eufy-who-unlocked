"""The eufy Who Unlocked integration.

Read-only: reports WHO last unlocked the eufy FamiLock S3 Max, and by
which method, from a small bridge process that listens to eufy FCM
push on the user's Mac Mini. No camera, motion, battery, lock entity,
or lock/unlock service — and no eufy credentials anywhere in Home
Assistant. The config flow asks only for the bridge URL.

Honest polling model: the eufy side is push (the bridge polls eufy
once at startup, then push-only). The Home Assistant side polls the
bridge's in-memory latest unlock on a slow interval — see
DEFAULT_SCAN_INTERVAL in const.py for why 5 minutes is enough.
Automations that need the unlock the moment it happens should
trigger on the Matter lock unlock (lock.eufy_familock_s3_max) and
read the sensor, correlating within CORRELATION_WINDOW_S.
"""
from dataclasses import dataclass
from datetime import timedelta
import logging
from typing import Any

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    BRIDGE_TIMEOUT_S,
    CONF_BRIDGE_URL,
    DEFAULT_BRIDGE_URL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    ENDPOINT_LAST_UNLOCK,
    PLATFORMS,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class EufyWhoUnlockedData:
    """Runtime data for the eufy Who Unlocked config entry."""

    coordinator: DataUpdateCoordinator


def _normalise_bridge_url(url: str) -> str:
    """Strip a trailing slash so endpoint joins are predictable."""
    return url.rstrip("/")


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up eufy Who Unlocked from a config entry."""
    session = async_get_clientsession(hass)
    bridge_url = _normalise_bridge_url(
        entry.data.get(CONF_BRIDGE_URL, DEFAULT_BRIDGE_URL)
    )

    async def async_update_data() -> dict[str, Any] | None:
        """Fetch the latest unlock from the bridge.

        Returns the /last-unlock payload, or None when the bridge has
        not seen an unlock yet (HTTP 404 — a normal state, not an
        error). Connection problems raise UpdateFailed so the
        entities go unavailable instead of showing stale data.
        """
        try:
            async with session.get(
                f"{bridge_url}{ENDPOINT_LAST_UNLOCK}",
                timeout=aiohttp.ClientTimeout(total=BRIDGE_TIMEOUT_S),
            ) as response:
                if response.status == 404:
                    # Bridge is up but no unlock seen yet.
                    return None
                if response.status != 200:
                    raise UpdateFailed(
                        f"Bridge returned HTTP {response.status} for /last-unlock"
                    )
                data = await response.json()
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"Error communicating with who-unlocked bridge: {err}") from err
        except TimeoutError as err:
            raise UpdateFailed(f"Timed out talking to who-unlocked bridge: {err}") from err

        if not isinstance(data, dict):
            raise UpdateFailed("Bridge returned an unexpected /last-unlock payload")
        return data

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=f"{DOMAIN}_{entry.entry_id}",
        update_method=async_update_data,
        update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
    )

    # First refresh must succeed even when no unlock has been seen
    # yet (404 -> None data), so this never raises for that case.
    await coordinator.async_config_entry_first_refresh()

    runtime_data = EufyWhoUnlockedData(coordinator=coordinator)

    # Support modern HA runtime_data
    entry.runtime_data = runtime_data

    # Backwards compatibility with hass.data
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "coordinator": coordinator,
        "runtime_data": runtime_data,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await hass.config_entries.async_reload(entry.entry_id)
