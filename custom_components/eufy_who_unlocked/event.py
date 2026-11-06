"""Event platform for the eufy Who Unlocked integration.

One event entity: fires `eufy_unlock` whenever the
coordinator sees a NEW unlock — i.e. a push arrives whose
`at_epoch_ms` differs from the previous one. The event carries the
same attributes as the sensor (who, method, eufy_event_type, exterior,
timing, short_user_id, locked). The device-page Events card
displays the HA event-type string, so it is kept short on
purpose (see const.py for why it is not the bare "unlock");
`logbook.py` renders the same event as a readable sentence
in the logbook / Activity list.

Because the coordinator is fed by FCM push, this fires ~2s after
the unlock, at push time — not on a poll. Lock-only pushes (events
262-269) update the `locked` attribute without firing: their epoch
does not change the last unlock.

No event fires at startup/restart for an unlock already seen: the
last seen epoch is seeded from the coordinator data at setup, so
only a genuinely new unlock triggers.
"""
from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, EVENT_TYPE_UNLOCK
from .entity import EufyWhoUnlockedBaseEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the eufy Who Unlocked event platform."""
    data = entry.runtime_data if getattr(entry, "runtime_data", None) else hass.data[DOMAIN][entry.entry_id]
    coordinator = data.coordinator if hasattr(data, "coordinator") else data["coordinator"]

    async_add_entities([FrontDoorUnlockEvent(coordinator, entry)])


class FrontDoorUnlockEvent(EufyWhoUnlockedBaseEntity, EventEntity):
    """Event entity firing on each new front-door unlock."""

    _attr_name = "Unlock"
    _attr_translation_key = "unlock"
    _attr_icon = "mdi:door-open"
    _attr_event_types = [EVENT_TYPE_UNLOCK]

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        """Initialize the event entity."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_front_door_unlock"
        # Seed from whatever the coordinator already knows so a
        # restart does not re-fire an old unlock as a new event.
        data = coordinator.data or {}
        self._last_seen_epoch_ms = data.get("at_epoch_ms")

    @callback
    def _handle_coordinator_update(self) -> None:
        """Fire the HA event when a new unlock appears.

        Order matters (v0.2.8): trigger FIRST, then let the
        coordinator write state. HA core's ``EventEntity.
        _trigger_event()`` only sets internal fields
        (``__last_event_triggered`` / ``__last_event_type`` /
        ``__last_event_attributes``) — it does NOT write state.
        ``CoordinatorEntity._handle_coordinator_update()`` is the
        write (``async_write_ha_state()``), so calling ``super()``
        first snapshots the PREVIOUS unlock's state/event_type
        and the new event stays invisible until the next update
        — the live v0.2.7 bug, where the sensors showed the
        12:00 AM unlock while the event entity still showed the
        11:39 PM one. Triggering first means the single write
        that follows carries the new event. No second write is
        needed and the event is never double-fired.
        """
        data = self.coordinator.data or {}
        epoch_ms = data.get("at_epoch_ms")
        if epoch_ms is not None and epoch_ms != self._last_seen_epoch_ms:
            self._last_seen_epoch_ms = epoch_ms
            self._trigger_event(
                EVENT_TYPE_UNLOCK,
                {
                    "who": data.get("who") or data.get("nick_name"),
                    "method": data.get("method"),
                    "method_label": data.get("method_label"),
                    "eufy_event_type": data.get("eufy_event_type"),
                    "exterior": data.get("exterior"),
                    "at_iso": data.get("at_iso"),
                    "at_epoch_ms": epoch_ms,
                    "short_user_id": data.get("short_user_id"),
                    "locked": data.get("locked"),
                },
            )
        super()._handle_coordinator_update()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the latest unlock alongside the event state."""
        data = self.coordinator.data or {}
        return {
            "who": data.get("who") or data.get("nick_name"),
            "method": data.get("method"),
            "method_label": data.get("method_label"),
            "eufy_event_type": data.get("eufy_event_type"),
            "exterior": data.get("exterior"),
            "at_iso": data.get("at_iso"),
            "at_epoch_ms": data.get("at_epoch_ms"),
            "short_user_id": data.get("short_user_id"),
            "locked": data.get("locked"),
        }
