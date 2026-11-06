"""The one place the eufy cloud client and session cache are built.

Both the config flow and ``async_setup_entry`` reach the library
through here, so the construction (and the storage layout) cannot
drift between the two. The pattern mirrors alarun's
``homeassistant-eufy-home-security`` ``runtime.py``:

- The session cache lives in a private HA ``Store`` keyed by a
  sha256 prefix of the e-mail — the address itself never appears in
  a ``.storage`` file name. ``private=True`` because the document
  holds the account password, the cloud session and the FCM
  credentials; ``atomic_writes=True`` because a crash mid-write
  would lose the session and the install's identity.
- The cache is keyed by account, not by entry id: the flow has no
  entry id yet, and an account key lets setup reuse the session the
  flow's login cached.
- The password is kept ONLY in the cache (``cache.set_password``),
  never in the config entry data.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from eufy_home_security.cloud.api import EufyCloudApi
from eufy_home_security.storage import SessionCache

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

_STORE_VERSION = 1


def store_key(email: str) -> str:
    """The storage key of one eufy account's cache document.

    A sha256 prefix of the normalised e-mail, so the address itself
    never appears in a ``.storage`` file name.
    """
    account = hashlib.sha256(email.strip().lower().encode()).hexdigest()[:16]
    return f"{DOMAIN}.{account}"


def cache_store(hass: HomeAssistant, email: str) -> Store[dict[str, Any]]:
    """The account's private store, shared by the config flow and the entry."""
    return Store(
        hass,
        _STORE_VERSION,
        store_key(email),
        private=True,
        atomic_writes=True,
    )


async def async_build_cache(hass: HomeAssistant, email: str) -> SessionCache:
    """Load (or create) the account's session cache."""
    cache = SessionCache(cache_store(hass, email), email)
    await cache.async_load()
    return cache


def build_cloud(
    hass: HomeAssistant,
    cache: SessionCache,
    email: str,
    password: str | None,
    country: str,
) -> EufyCloudApi:
    """Build the cloud client on the account's cache.

    ``password`` is what the user typed in a flow, and None at entry
    setup, where the library uses the password its last successful
    login cached. The client sends Home Assistant's time zone, as
    the eufy app sends the phone's.
    """
    return EufyCloudApi(
        async_get_clientsession(hass),
        cache,
        email,
        password,
        country=country or "",
        timezone=hass.config.time_zone,
    )


async def async_login_and_find_lock(
    cloud: EufyCloudApi, lock_serial: str
) -> Any | None:
    """Log in, fetch the device list once, and find the lock by serial.

    Returns the matching ``CloudDevice`` (or None when the account
    has no device with that serial). Raises the library's login
    errors as they are — ``LoginChallengeError`` for a 2FA/captcha
    challenge, ``AuthenticationError`` for bad credentials.
    """
    await cloud.async_login()
    devices = await cloud.async_get_devices(refresh=True)
    wanted = lock_serial.strip().upper()
    for device in devices:
        serial = getattr(device, "device_sn", "") or ""
        if serial.strip().upper() == wanted:
            return device
    _LOGGER.debug(
        "lock serial %s not in the account device list (%d devices)",
        lock_serial[:5] + "…",
        len(devices),
    )
    return None
