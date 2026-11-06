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
- The token is registered in two places, not one: the library's
  mega push service AND the security-app host (see
  ``security_push.py``). The mega call alone left the sensor
  ``unknown`` after real unlocks in live HA; the Node bridge,
  which makes both, received the same unlocks live.
- TIMING TRAP (v0.2.1 bug, fixed in v0.2.2): the library's
  ``PushListener._start_client`` calls ``on_token_upload`` BEFORE
  it assigns ``self._token`` — so a hook that reads
  ``listener.token`` inside that callback always sees ``None``
  and skips the security-app registration. The registration is
  therefore triggered from ``on_listening`` (which fires after
  ``_start_client`` returns, token set) and, as a fallback, from
  ``on_token_upload`` via a task that WAITS for the token to
  appear (poll 0.5s, up to 10s). Either path is deduplicated per
  token; a listening drop resets the dedupe so a supervisor
  restart re-registers.
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
from .security_push import async_register_security_push_token

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
    # Diagnostic visibility (v0.2.3): the library's MCS/push logs
    # live under eufy_home_security.push.fcm, a different logger
    # from this integration — a user searching HA Logs for
    # "eufy_who_unlocked" never saw them, so "no log lines" could
    # not distinguish "MCS socket never up" from "push arrived
    # but was dropped" from "push never sent". Raising that
    # logger to DEBUG surfaces its MCS connect/login and
    # "push event:" lines in HA's log UI. It is chatty only on
    # connect/reconnect and on actual pushes (infrequent here).
    logging.getLogger("eufy_home_security.push.fcm").setLevel(logging.DEBUG)
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

    # Push diagnostics (v0.2.3): counters visible on the sensor
    # and in the log, so a live "no activity" report can be
    # distinguished without guessing: seen=0 means the library
    # never delivered a push to our callback (MCS down, or eufy
    # never sent to this token); seen>0 with dropped>0 means a
    # push arrived but our mapper rejected it (reason logged);
    # seen>0 mapped>0 means pushes work and the sensor should
    # have updated. Never holds a token, password, or user_id.
    from datetime import datetime as _dt, timezone as _tz

    push_diag: dict[str, Any] = {
        "seen": 0,
        "mapped": 0,
        "dropped": 0,
        "listening": False,
        "last_seen_iso": None,
        "last_drop_reason": None,
    }

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
    coordinator.push_diag = push_diag  # type: ignore[attr-defined]

    def _on_push(event: Any) -> None:
        """PushListener callback (sync): map the push, feed the coordinator.

        Logs EVERY delivery at INFO (v0.2.3 diagnostic): before
        v0.2.3 this only logged mapped unlocks at DEBUG, so a
        push dropped by the library or by our mapper was
        invisible — "no activity" with no log lines at all.
        """
        from .lock_push import extract_lock_payload

        push_diag["seen"] += 1
        push_diag["last_seen_iso"] = _dt.now(_tz.utc).isoformat(timespec="seconds")
        # What the library delivered, before our mapping: event
        # type + serial (redacted to first 5 chars) + time. Never
        # a token, password, or user_id.
        raw_sn = getattr(event, "device_sn", None)
        _LOGGER.info(
            "eufy push delivered: event_type=%s device_sn=%s event_time_ms=%s",
            getattr(event, "event_type", None),
            (str(raw_sn)[:5] + "…") if raw_sn else None,
            getattr(event, "event_time_ms", None),
        )
        mapped = handle_push_event(event, lock_serial=lock_serial, tracker=tracker)
        if mapped is None:
            push_diag["dropped"] += 1
            _sn, inner = extract_lock_payload(event)
            if not inner:
                reason = "no inner payload"
            elif _sn is None:
                reason = "no device serial on push"
            elif _sn != lock_serial:
                reason = f"wrong device serial ({str(_sn)[:5]}…)"
            else:
                reason = (
                    f"unknown event_type={inner.get('event_type')} "
                    f"or no event_time"
                )
            push_diag["last_drop_reason"] = reason
            _LOGGER.info("eufy push dropped by mapper: %s", reason)
            return
        push_diag["mapped"] += 1
        if mapped["kind"] == "unlock":
            coordinator.async_set_updated_data(mapped)
            _LOGGER.info(
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
            _LOGGER.info(
                "lock event: %s (event %s) locked=%s",
                mapped["method"],
                mapped["event_type"],
                mapped["locked"],
            )

    # Security-app push registration state. The library registers
    # the FCM token on the mega push service only; the Node SDK —
    # which received live lock pushes where this integration saw
    # none — also registers it on the security-app host (see
    # security_push.py). The registration is triggered from two
    # callbacks (see below) and deduplicated here: ``last_token``
    # is the token most recently registered successfully, and
    # ``in_progress_token`` is the token a registration is
    # currently running for. A listening drop resets
    # ``last_token`` so a supervisor restart re-registers even
    # for the same token.
    listener_holder: dict[str, Any] = {"listener": None}
    security_state: dict[str, Any] = {
        "running": False,
        "in_progress_token": None,
        "last_token": None,
        "wait_task": None,
    }

    def _security_token() -> str | None:
        listener = listener_holder.get("listener")
        return getattr(listener, "token", None) if listener is not None else None

    def _schedule_security_registration(*, wait_for_token: bool, source: str) -> None:
        """Run the security-app registration for the current FCM token.

        Called from the (sync) PushListener callbacks. Never reads
        the token once and gives up: when ``wait_for_token`` is
        true and no token is available yet — the library fires
        ``on_token_upload`` BEFORE assigning ``self._token`` — a
        background task polls for the token (0.5s, up to 10s) and
        retries the scheduling then. A registration for a token
        already registered (or already in progress) is skipped.
        """
        token = _security_token()
        if token is None:
            if not wait_for_token:
                _LOGGER.warning(
                    "eufy push token uploaded but no FCM token is available; "
                    "skipping security-app push registration"
                )
                return
            wait_task = security_state.get("wait_task")
            if wait_task is not None and not wait_task.done():
                return  # a wait is already pending

            async def _wait_for_token() -> None:
                for _ in range(20):  # 0.5s x 20 = 10s
                    await asyncio.sleep(0.5)
                    if hass.data.get(DOMAIN, {}).get(entry.entry_id) is None:
                        return  # entry unloaded while waiting
                    if _security_token() is not None:
                        _schedule_security_registration(
                            wait_for_token=False, source="token_upload_wait"
                        )
                        return
                _LOGGER.warning(
                    "eufy push token still not available after 10s wait; "
                    "skipping security-app push registration"
                )

            security_state["wait_task"] = hass.async_create_task(_wait_for_token())
            return
        if token == security_state["last_token"]:
            _LOGGER.debug(
                "eufy security-app push registration already done for this "
                "token (%s); skipping",
                source,
            )
            return
        if security_state["running"] or token == security_state["in_progress_token"]:
            return
        security_state["running"] = True
        security_state["in_progress_token"] = token

        async def _register() -> None:
            try:
                if hass.data.get(DOMAIN, {}).get(entry.entry_id) is None:
                    return  # entry unloaded before the task ran
                try:
                    region = cloud.device_region(lock_serial)
                except Exception:  # noqa: BLE001 - fall back to the first region
                    region = getattr(cloud, "region", "us") or "us"
                results = await async_register_security_push_token(
                    cloud, token, region=region
                )
                _LOGGER.info("eufy security-app push registration: %s", results)
                # Only a completed registration (even if an endpoint
                # reported failed — the attempt happened) counts as
                # "done" for dedupe; a raised exception leaves
                # last_token unset so the next trigger retries.
                security_state["last_token"] = token
            except Exception:  # noqa: BLE001 - degraded, never fatal
                _LOGGER.warning(
                    "eufy security-app push registration failed", exc_info=True
                )
            finally:
                security_state["running"] = False
                security_state["in_progress_token"] = None

        hass.async_create_task(_register())

    listening_state = {"listening": False, "ever_listened": False}

    def _on_listening(running: bool, error: EufySecurityError | None) -> None:
        """PushListener listening callback: catch up once per reconnect.

        Also the primary trigger for the security-app registration:
        the library calls this AFTER ``_start_client`` returns, so
        ``listener.token`` is assigned by now — unlike
        ``on_token_upload``, which fires before the assignment.
        """
        was = listening_state["listening"]
        listening_state["listening"] = running
        push_diag["listening"] = running
        # INFO (v0.2.3): before this, a first start logged
        # nothing in this integration — the library's "push
        # listener started" line lives under a different logger
        # the user never saw, so MCS-up vs MCS-down was invisible.
        _LOGGER.info(
            "eufy push listening: %s (token present: %s)",
            "started" if running else f"stopped: {error}",
            _security_token() is not None,
        )
        if running:
            # A fresh MCS session: (re-)register the token on the
            # security-app host. Deduplicated per token, so the
            # token-upload trigger firing just before this one does
            # not double-register.
            _schedule_security_registration(wait_for_token=False, source="listening")
            if not listening_state["ever_listened"]:
                listening_state["ever_listened"] = True
                return  # first start: the startup poll already ran
            if not was:
                # Reconnect after a drop: one catch-up poll, then
                # back to push-only. (It cannot recover who
                # unlocked in the gap.)
                _LOGGER.info("eufy push reconnected; running one catch-up poll")
                hass.async_create_task(coordinator.async_refresh())
        else:
            # The session ended: the next start is a new session,
            # so allow re-registration for the same token then.
            security_state["last_token"] = None
            _LOGGER.warning("eufy push stopped listening: %s", error)

    def _on_token_upload(error: Any) -> None:
        """PushListener token-upload callback (sync): run the extra registration.

        WARNING — timing: the library fires this BEFORE it assigns
        ``listener.token`` (``_start_client`` calls
        ``_report_token_upload`` and only then sets ``self._token``),
        so reading the token here directly always yields ``None``.
        ``_schedule_security_registration`` therefore waits for the
        token to appear instead of giving up immediately (v0.2.1
        read it once, logged "no FCM token is available", and the
        security-app calls never ran — the live "no activity" bug).
        """
        if error is not None:
            _LOGGER.warning("eufy mega push token upload failed: %s", error)
            return
        _schedule_security_registration(wait_for_token=True, source="token_upload")

    listener = PushListener(
        cloud,
        cache,
        _on_push,
        # The same HA-managed session the cloud client uses
        # (build_cloud passes async_get_clientsession(hass)).
        session=async_get_clientsession(hass),
        on_token_upload=_on_token_upload,
        on_listening=_on_listening,
    )
    listener_holder["listener"] = listener

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
