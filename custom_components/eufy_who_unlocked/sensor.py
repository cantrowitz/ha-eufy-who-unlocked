"""Sensor platform for the eufy Who Unlocked integration.

Two sensors, both fed by the same coordinator data (eufy FCM
push, ~2s after the unlock — not on a poll):
- who last unlocked the front door (state = person's name as
  eufy reports it, e.g. "Emma"), with the unlock method and
  timing as attributes;
- how the door was last unlocked, as an ENUM sensor (state is
  one of the model's unlock methods, see
  ``lock_push.MODEL_UNLOCK_METHODS``).
State is "unknown" until the first push unlock arrives.

Caveats, stated plainly:
- Manual / thumbturn (event 257) reports the account holder's name
  ("cantrowitz"), not whoever turned it — it is anonymous in
  practice. Its `exterior` attribute is false.
- An exterior unlock means that person ARRIVED. It does not prove
  they are still home — leaving produces no unlock event, and there
  is no presence entity here.
- App unlocks (261) can be remote: pair an app unlock with the
  Matter lock-unlock trigger within 10 seconds before treating it
  as an arrival.
"""
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_LOCK_SERIAL, DOMAIN
from .entity import EufyWhoUnlockedBaseEntity
from .lock_push import UNLOCK_METHODS, model_id_for_serial, unlock_methods_for_model


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the eufy Who Unlocked sensor platform."""
    data = entry.runtime_data if getattr(entry, "runtime_data", None) else hass.data[DOMAIN][entry.entry_id]
    coordinator = data.coordinator if hasattr(data, "coordinator") else data["coordinator"]

    async_add_entities([
        FrontDoorLastUnlockSensor(coordinator, entry),
        FrontDoorUnlockMethodSensor(coordinator, entry),
    ])


class FrontDoorLastUnlockSensor(EufyWhoUnlockedBaseEntity, SensorEntity):
    """Sensor reporting who last unlocked the front door."""

    _attr_name = "Front door last unlock"
    _attr_translation_key = "front_door_last_unlock"
    _attr_icon = "mdi:lock-open-variant"

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_front_door_last_unlock"

    @property
    def native_value(self) -> str | None:
        """Return who last unlocked, or None (state "unknown") before the first unlock."""
        data = self.coordinator.data or {}
        # `who` is the trimmed nick_name from the push; fall back to
        # the raw nick_name field if a future version omits it.
        return data.get("who") or data.get("nick_name") or None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the unlock method, timing and lock state."""
        data = self.coordinator.data or {}
        # Push diagnostics (v0.2.3): visible in the HA UI, so a
        # live "no activity" report is checkable without logs —
        # push_events_seen=0 means the library never delivered a
        # push to our callback at all.
        diag = getattr(self.coordinator, "push_diag", {}) or {}
        return {
            "method": data.get("method"),
            "method_label": data.get("method_label"),
            "event_type": data.get("event_type"),
            "exterior": data.get("exterior"),
            "at_iso": data.get("at_iso"),
            "at_epoch_ms": data.get("at_epoch_ms"),
            "short_user_id": data.get("short_user_id"),
            "locked": data.get("locked"),
            "push_listening": diag.get("listening"),
            "push_events_seen": diag.get("seen"),
            "push_events_mapped": diag.get("mapped"),
            "push_events_dropped": diag.get("dropped"),
            "last_push_at": diag.get("last_seen_iso"),
            "last_push_drop_reason": diag.get("last_drop_reason"),
        }


class FrontDoorUnlockMethodSensor(EufyWhoUnlockedBaseEntity, SensorEntity):
    """Sensor reporting HOW the front door was last unlocked, as an enum.

    State is one of UNLOCK_METHODS — ["manual", "auto", "passcode",
    "fingerprint", "app", "palm"] — derived from lock_push.EVENT_MAP,
    so the valid set is visible in the HA UI and in automations
    instead of a free-text string to guess. Who cannot be an enum
    the same way: names are dynamic, they come from eufy per
    enrolled user, not from a fixed list.

    Defensive: a method not in UNLOCK_METHODS (e.g. a future event
    type the mapper does not know yet) reports unknown, never the
    raw string — an enum sensor must never state a value outside
    its options.
    """

    _attr_name = "Front door unlock method"
    _attr_translation_key = "front_door_unlock_method"
    _attr_icon = "mdi:key-variant"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = UNLOCK_METHODS

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        """Initialize the method sensor.

        Options come from the lock model, not a global list: the
        model is the serial's 5-character prefix (the same value
        the startup poll's device object reports as
        ``CloudDevice.model_id``), looked up in
        ``lock_push.MODEL_UNLOCK_METHODS`` — an unlisted model falls
        back to the full observed set. Set per instance, so two
        locks of different models would get different option sets.
        """
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_front_door_unlock_method"
        self._attr_options = unlock_methods_for_model(
            model_id_for_serial(entry.data.get(CONF_LOCK_SERIAL))
        )

    @property
    def native_value(self) -> str | None:
        """Return the unlock method, or None (unknown) before the first unlock."""
        data = self.coordinator.data or {}
        method = data.get("method")
        return method if method in self._attr_options else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose who unlocked and the timing alongside the method."""
        data = self.coordinator.data or {}
        return {
            "who": data.get("who") or data.get("nick_name"),
            "method_label": data.get("method_label"),
            "event_type": data.get("event_type"),
            "exterior": data.get("exterior"),
            "at_iso": data.get("at_iso"),
            "at_epoch_ms": data.get("at_epoch_ms"),
            "short_user_id": data.get("short_user_id"),
            "locked": data.get("locked"),
        }
