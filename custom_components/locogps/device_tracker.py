"""Location of LocoGPS trackers."""

from __future__ import annotations

from typing import Any

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import LocoGPSConfigEntry, LocoGPSCoordinator
from .entity import LocoGPSEntity, add_entities_when_new

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LocoGPSConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one tracker entity per device."""

    def build(coordinator: LocoGPSCoordinator):
        for device_id in coordinator.data.devices:
            yield (
                f"{device_id}",
                lambda device_id=device_id: LocoGPSTracker(coordinator, device_id),
            )

    add_entities_when_new(entry, async_add_entities, build)


class LocoGPSTracker(LocoGPSEntity, TrackerEntity):
    """Where the tracker is.

    Uses the latest trustworthy location, see has_usable_location.
    """

    _attr_name = None
    _attr_translation_key = "tracker"

    def __init__(self, coordinator: LocoGPSCoordinator, device_id: int) -> None:
        """Initialize the tracker."""
        super().__init__(coordinator, device_id, "tracker")

    @property
    def _location(self) -> dict[str, Any]:
        return self.coordinator.data.locations.get(self.device_id) or {}

    @property
    def source_type(self) -> SourceType:
        """Return the source of the location."""
        return SourceType.GPS

    @property
    def latitude(self) -> float | None:
        """Return the latitude."""
        return self._location.get("latitude")

    @property
    def longitude(self) -> float | None:
        """Return the longitude."""
        return self._location.get("longitude")

    @property
    def location_accuracy(self) -> float:
        """Return the accuracy in meters, 0 when the tracker does not report one."""
        return float(self._location.get("accuracy") or 0)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return when the location was measured."""
        return {"fix_time": self._location.get("fixTime")}
