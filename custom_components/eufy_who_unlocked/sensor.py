"""Sensor platform for the eufy Who Unlocked integration.

One sensor: who last unlocked the front door. The state is the
person's name as eufy reports it (the bridge's `who` field — the
trimmed nick_name, e.g. "Emma"), with the unlock method and timing
as attributes. State is "unknown" until the bridge has seen its
first unlock.

Caveats, stated plainly:
- Manual / thumbturn (event 257) reports the account holder's name
  ("cantrowitz"), not whoever turned it — it is anonymous in
  practice. Its `exterior` attribute is false.
- An exterior unlock means that person ARRIVED. It does not prove
  they are still home — leaving produces no unlock event, and there
  is no presence entity here.
"""
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import EufyWhoUnlockedBaseEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the eufy Who Unlocked sensor platform."""
    data = entry.runtime_data if getattr(entry, "runtime_data", None) else hass.data[DOMAIN][entry.entry_id]
    coordinator = data.coordinator if hasattr(data, "coordinator") else data["coordinator"]

    async_add_entities([FrontDoorLastUnlockSensor(coordinator, entry)])


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
        # `who` is the trimmed nick_name from the bridge; fall back to
        # the raw nick_name field if a future bridge version omits it.
        return data.get("who") or data.get("nick_name") or None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the unlock method, timing and lock state."""
        data = self.coordinator.data or {}
        return {
            "method": data.get("method"),
            "method_label": data.get("method_label"),
            "event_type": data.get("event_type"),
            "exterior": data.get("exterior"),
            "at_iso": data.get("at_iso"),
            "at_epoch_ms": data.get("at_epoch_ms"),
            "short_user_id": data.get("short_user_id"),
            "locked": data.get("locked"),
        }
