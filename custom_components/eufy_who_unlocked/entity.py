"""Base entity class for the eufy Who Unlocked integration."""
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DEVICE_MANUFACTURER,
    DEVICE_MODEL,
    DEVICE_NAME,
    DOMAIN,
)


class EufyWhoUnlockedBaseEntity(CoordinatorEntity):
    """Base entity attached to the who-unlocked bridge device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, entry) -> None:
        """Initialize the base entity."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=DEVICE_NAME,
            manufacturer=DEVICE_MANUFACTURER,
            model=DEVICE_MODEL,
        )

    @property
    def available(self) -> bool:
        """Return True if the entity is available.

        Data is None when the bridge is up but has not seen an unlock
        yet — that is a valid state, not unavailability, so only a
        failed coordinator update (last_update_success False) makes
        the entity unavailable.
        """
        return super().available
