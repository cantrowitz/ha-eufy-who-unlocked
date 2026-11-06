"""Describe logbook events for eufy Who Unlocked.

Home Assistant's logbook calls ``async_describe_events`` (when
the logbook component is enabled) to learn how to render an
integration's bus events as human-readable entries. Without it,
the logbook shows the raw event-type string for each unlock.
With it, an unlock reads as a sentence built from the event
data the event entity already fires:

    Unlocked by Alex via Palm / vein

The event entity's own display is a separate thing: the
device-page Events card always shows the HA event-type string
itself (``eufy_unlock`` — short on purpose, see const.py), so
this renderer does not change that card. It changes the
logbook / Activity-list entry for the same unlock.

This file is only loaded by HA's logbook component; if logbook
is disabled it is inert. It never raises: a malformed event
still renders as the plain word "Unlocked".
"""
from __future__ import annotations

from collections.abc import Callable

from homeassistant.components.logbook import (
    LOGBOOK_ENTRY_ENTITY_ID,
    LOGBOOK_ENTRY_MESSAGE,
    LOGBOOK_ENTRY_NAME,
)
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import Event, HomeAssistant, callback

from .const import DOMAIN, EVENT_TYPE_UNLOCK


@callback
def async_describe_events(
    hass: HomeAssistant,
    async_describe_event: Callable[
        [str, str, Callable[[Event], dict[str, str | None]]], None
    ],
) -> None:
    """Register the logbook renderer for unlock events."""

    @callback
    def async_describe_logbook_event(event: Event) -> dict[str, str | None]:
        """Render one unlock bus event as a readable sentence."""
        try:
            data = getattr(event, "data", None) or {}
            if not isinstance(data, dict):
                data = {}
            who = data.get("who") or data.get("nick_name") or None
            method = data.get("method_label") or data.get("method") or None
            if who and method:
                message = f"Unlocked by {who} via {method}"
            elif who:
                message = f"Unlocked by {who}"
            elif method:
                message = f"Unlocked via {method}"
            else:
                message = "Unlocked"
            return {
                LOGBOOK_ENTRY_NAME: "eufy Who Unlocked",
                LOGBOOK_ENTRY_MESSAGE: message,
                LOGBOOK_ENTRY_ENTITY_ID: data.get(ATTR_ENTITY_ID),
            }
        except Exception:  # noqa: BLE001 — logbook rendering must never raise
            return {
                LOGBOOK_ENTRY_NAME: "eufy Who Unlocked",
                LOGBOOK_ENTRY_MESSAGE: "Unlocked",
                LOGBOOK_ENTRY_ENTITY_ID: None,
            }

    async_describe_event(DOMAIN, EVENT_TYPE_UNLOCK, async_describe_logbook_event)
