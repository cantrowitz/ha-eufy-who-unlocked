"""The eufy Who Unlocked integration.

Read-only: reports WHO last unlocked the eufy FamiLock S3 Max, and
by which method, straight from eufy FCM push — no bridge, no Mac
Mini, no extra process. Home Assistant itself holds the eufy
session (private HA Store-backed ``SessionCache``) and runs
alarun's ``PushListener`` (``eufy_home_security`` 0.4.12, push stack
only — no ``EufySecurity`` client, no P2P/discovery/guard
machinery).

No camera, motion, battery, lock entity, or lock/unlock service.
Matter stays the lock entity; this integration is additive.

Push model, honestly stated:
- The cloud is polled ONCE at startup (``async_get_devices``) to
  confirm the configured lock serial is on the account. The device
  list carries no lock state, so ``locked`` starts unknown and is
  set by the first push (param 6000).
- After that it is push-only. The listener re-uploads the FCM token
  on every start and supervises its own MCS socket (30s checks,
  doubling backoff). On a reconnect the coordinator does one
  catch-up device-list poll — that can only re-seed
  locked/unlocked, never recover WHO unlocked during a gap.
  Missed unlocks stay missed.
- An optional 10-minute reconciliation poll (options flow, off by
  default) re-asks the device list to confirm the cloud is still
  reachable; it likewise cannot recover who unlocked.

Automations that need the unlock the moment it happens should
trigger on the Matter lock unlock (``lock.eufy_familock_s3_max``)
and read the sensor, correlating the two within
``CORRELATION_WINDOW_S`` (10s).

Network requirement: the HA host must reach
``android.clients.google.com``, ``firebaseinstallations.googleapis.com``
and ``mtalk.google.com:5228`` direct TLS with SNI (no HTTP proxy
fallback). A host behind an egress proxy that blocks the FCM
checkin cannot register for push at all.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from eufy_home_security.exceptions import (
    AuthenticationError,
    EufySecurityError,
    LoginChallengeError,
)
from eufy_home_security.push.fcm import PushListener

from .const import (
    CONF_COUNTRY,
    CONF_EMAIL,
    CONF_LOCK_SERIAL,
    CONF_RECONCILE,
    DEFAULT_COUNTRY,
    DOMAIN,
    PLATFORMS,
    RECONCILE_INTERVAL_S,
)
from .lock_push import UnlockTracker, handle_push_event
from .runtime import async_build_cache, async_login_and_find_lock, build_cloud

_LOGGER = logging.getLogger(__name__)

# First-start push retry: 5s, doubling to a 300s cap. The listener
# supervises itself once it has listened; this loop only covers a
# first start that failed outright.
_PUSH_RETRY_MIN_S = 5.0
_PUSH_RETRY_MAX_S = 300.0


@dataclass
class EufyWhoUnlockedData:
    """Runtime data for the eufy Who Unlocked config entry."""

    coordinator: DataUpdateCoordinator
    cloud: Any
    cache: Any
    listener: PushListener
    lock_serial: str
    tracker: UnlockTracker


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up eufy Who Unlocked from a config entry."""
    email = entry.data[CONF_EMAIL]
    lock_serial = entry.data[CONF_LOCK_SERIAL]
    country = entry.data.get(CONF_COUNTRY, DEFAULT_COUNTRY)
    reconcile = bool(entry.options.get(CONF_RECONCILE, False))

    cache = await async_build_cache(hass, email)
    # None at setup: the library uses the password its last
    # successful login cached. No cached password + no cached
    # session surfaces as an auth error below, which triggers the
    # reauth flow.
    cloud = build_cloud(hass, cache, email, None, country)

    # The ONE startup poll: log in and confirm the lock serial is
    # on this account. Raises LoginChallengeError when eufy wants a
    # 2FA code / captcha again — that is a reauth, not a retry.
    try:
        device = await async_login_and_find_lock(cloud, lock_serial)
    except (LoginChallengeError, AuthenticationError) as err:
        raise ConfigEntryAuthFailed(f"eufy login needs attention: {err}") from err
    except EufySecurityError as err:
        raise ConfigEntryNotReady(f"eufy cloud not reachable at setup: {err}") from err
    if device is None:
        raise ConfigEntryError(
            f"Lock serial {lock_serial} was not found on the eufy account {email}. "
            "Check the serial and re-add the integration."
        )
    _LOGGER.info(
        "eufy Who Unlocked: lock %s found on the account",
        getattr(device, "name", lock_serial),
    )
    await cache.async_save()

    tracker = UnlockTracker()

    async def async_reconcile() -> dict[str, Any] | None:
        """Catch-up / reconciliation poll: re-ask the device list.

        This only confirms the cloud is reachable and the lock is
        still listed. The device list carries no lock state, so the
        last unlock and the locked flag are left exactly as the
        last push set them. A poll can never recover WHO unlocked
        during a push gap.
        """
        try:
            devices = await cloud.async_get_devices(refresh=True)
        except EufySecurityError as err:
            raise UpdateFailed(f"eufy device list refresh failed: {err}") from err
        wanted = lock_serial.strip().upper()
        if not any(
            (getattr(d, "device_sn", "") or "").strip().upper() == wanted
            for d in devices
        ):
            raise UpdateFailed(
                f"Lock serial {lock_serial} is no longer listed on the eufy account"
            )
        return coordinator.data

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=f"{DOMAIN}_{entry.entry_id}",
        update_method=async_reconcile,
        # Push-only by default: no periodic polling. The options
        # flow can switch on a 10-minute reconciliation poll.
        update_interval=timedelta(seconds=RECONCILE_INTERVAL_S) if reconcile else None,
    )
    # No first refresh: the startup poll above already confirmed
    # the account and the lock. Coordinator data stays None ("no
    # unlock seen yet") until the first push arrives.

    def _on_push(event: Any) -> None:
        """PushListener callback (sync): map the push, feed the coordinator."""
        mapped = handle_push_event(event, lock_serial=lock_serial, tracker=tracker)
        if mapped is None:
            return
        if mapped["kind"] == "unlock":
            coordinator.async_set_updated_data(mapped)
            _LOGGER.debug(
                "unlock: %s via %s (event %s)",
                mapped["who"],
                mapped["method"],
                mapped["event_type"],
            )
        else:
            # Lock-only event: update the locked flag without
            # touching the last unlock (or its event epoch, so the
            # event entity does not re-fire).
            current = dict(coordinator.data or {})
            current["locked"] = mapped["locked"]
            coordinator.async_set_updated_data(current)

    listening_state = {"listening": False, "ever_listened": False}

    def _on_listening(running: bool, error: EufySecurityError | None) -> None:
        """PushListener listening callback: catch up once per reconnect."""
        was = listening_state["listening"]
        listening_state["listening"] = running
        if running and not listening_state["ever_listened"]:
            listening_state["ever_listened"] = True
            return  # first start: the startup poll already ran
        if running and not was:
            # Reconnect after a drop: one catch-up poll, then back
            # to push-only. (It cannot recover who unlocked in the gap.)
            _LOGGER.info("eufy push reconnected; running one catch-up poll")
            hass.async_create_task(coordinator.async_refresh())
        elif not running:
            _LOGGER.warning("eufy push stopped listening: %s", error)

    listener = PushListener(
        cloud,
        cache,
        _on_push,
        # The same HA-managed session the cloud client uses
        # (build_cloud passes async_get_clientsession(hass)).
        session=async_get_clientsession(hass),
        on_listening=_on_listening,
    )

    runtime_data = EufyWhoUnlockedData(
        coordinator=coordinator,
        cloud=cloud,
        cache=cache,
        listener=listener,
        lock_serial=lock_serial,
        tracker=tracker,
    )

    # Support modern HA runtime_data
    entry.runtime_data = runtime_data

    # Backwards compatibility with hass.data
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime_data

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    # Push start runs in the background: registration (checkin +
    # Firebase Installations + GCM register + token upload) can
    # take up to the library's 60s start deadline, and setup must
    # not block on it. Retry with doubling backoff until it
    # listens; the listener supervises itself after that.
    async def _start_push_with_retry() -> None:
        delay = _PUSH_RETRY_MIN_S
        # The task is cancelled when the entry unloads; the
        # hass.data check is a second guard against a runaway loop.
        while hass.data.get(DOMAIN, {}).get(entry.entry_id) is runtime_data:
            try:
                await listener.async_start()
                return
            except asyncio.CancelledError:
                return
            except EufySecurityError as err:
                _LOGGER.warning(
                    "eufy push listener failed to start, retrying in %.0fs: %s",
                    delay,
                    err,
                )
                try:
                    await asyncio.sleep(delay)
                except asyncio.CancelledError:
                    return
                delay = min(delay * 2, _PUSH_RETRY_MAX_S)

    entry.async_create_background_task(
        hass, _start_push_with_retry(), "eufy who-unlocked push start"
    )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    runtime_data: EufyWhoUnlockedData | None = None
    if getattr(entry, "runtime_data", None):
        runtime_data = entry.runtime_data
    else:
        runtime_data = hass.data.get(DOMAIN, {}).get(entry.entry_id)

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok and runtime_data is not None:
        # Stop the listener, then persist the dedupe ring. The
        # background push-start task is cancelled with the entry.
        try:
            await runtime_data.listener.async_stop()
        except Exception:  # noqa: BLE001 - unload must not fail on listener teardown
            _LOGGER.debug("push listener stop raised during unload", exc_info=True)
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await hass.config_entries.async_reload(entry.entry_id)
