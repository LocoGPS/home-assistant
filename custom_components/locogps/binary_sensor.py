"""Binary sensors of LocoGPS trackers."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import WIFI_ZONE_SLOTS
from .coordinator import LocoGPSConfigEntry, LocoGPSCoordinator
from .entity import LocoGPSEntity, add_entities_when_new

PARALLEL_UPDATES = 0


def wifi_zone_name(device: dict[str, Any], position: dict[str, Any] | None) -> str | None:
    """Return the WLAN zone an LV2 is saving power in, or None.

    Same logic as the hint at the bottom of the LocoGPS app. It relies on the
    tracker's own report and not on the geofence.
    """
    status = ((position or {}).get("attributes") or {}).get("homeStatus")
    slot = WIFI_ZONE_SLOTS.get(status) if isinstance(status, int) else None
    if slot is None:
        return None
    name = slot.replace("slot", "Zone ")
    try:
        zones = json.loads((device.get("attributes") or {}).get("powerSaveZones") or "{}")
    except ValueError:
        zones = {}
    if isinstance(zones, dict) and isinstance(zones.get(slot), dict):
        name = zones[slot].get("name") or name
    return name


@dataclass(frozen=True, kw_only=True)
class LocoGPSBinarySensorDescription(BinarySensorEntityDescription):
    """Describes a LocoGPS binary sensor."""

    value_fn: Callable[[LocoGPSEntity], bool | None]
    supported_fn: Callable[[dict[str, Any]], bool] = lambda device: True


def _charging(entity: LocoGPSEntity) -> bool | None:
    value = entity.position_attributes.get("charge")
    return value if isinstance(value, bool) else None


BINARY_SENSORS: tuple[LocoGPSBinarySensorDescription, ...] = (
    LocoGPSBinarySensorDescription(
        key="online",
        translation_key="online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda entity: (entity.device or {}).get("status") == "online",
    ),
    LocoGPSBinarySensorDescription(
        key="charging",
        translation_key="charging",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        value_fn=_charging,
    ),
    LocoGPSBinarySensorDescription(
        key="wifi_energy_saving",
        translation_key="wifi_energy_saving",
        value_fn=lambda entity: wifi_zone_name(entity.device or {}, entity.position) is not None,
        supported_fn=lambda device: device.get("model") == "LV2",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LocoGPSConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the binary sensors of every device and one per linked geofence."""

    def build(coordinator: LocoGPSCoordinator):
        for device_id, device in coordinator.data.devices.items():
            for description in BINARY_SENSORS:
                if description.supported_fn(device):
                    yield (
                        f"{device_id}_{description.key}",
                        lambda device_id=device_id, description=description: LocoGPSBinarySensor(
                            coordinator, device_id, description
                        ),
                    )
            for geofence in coordinator.data.geofences.get(device_id, []):
                yield (
                    f"{device_id}_geofence_{geofence['id']}",
                    lambda device_id=device_id, geofence=geofence: LocoGPSGeofenceSensor(
                        coordinator, device_id, geofence
                    ),
                )

    add_entities_when_new(entry, async_add_entities, build)


class LocoGPSBinarySensor(LocoGPSEntity, BinarySensorEntity):
    """A state reported by the tracker."""

    entity_description: LocoGPSBinarySensorDescription

    def __init__(
        self,
        coordinator: LocoGPSCoordinator,
        device_id: int,
        description: LocoGPSBinarySensorDescription,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, device_id, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Return the state."""
        return self.entity_description.value_fn(self)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Name the WLAN zone while the tracker saves power."""
        if self.entity_description.key != "wifi_energy_saving":
            return None
        return {"wifi_zone": wifi_zone_name(self.device or {}, self.position)}


class LocoGPSGeofenceSensor(LocoGPSEntity, BinarySensorEntity):
    """Whether the tracker is inside one of its geofences.

    The server decides this, including its protection against old GPS fixes,
    so the state matches the LocoGPS notifications.
    """

    _attr_translation_key = "geofence"

    def __init__(
        self, coordinator: LocoGPSCoordinator, device_id: int, geofence: dict[str, Any]
    ) -> None:
        """Initialize the geofence sensor."""
        super().__init__(coordinator, device_id, f"geofence_{geofence['id']}")
        self.geofence_id = geofence["id"]
        self._attr_translation_placeholders = {"name": geofence.get("name") or str(geofence["id"])}

    @property
    def available(self) -> bool:
        """Return False once the geofence is no longer linked to the device."""
        linked = self.coordinator.data.geofences.get(self.device_id, [])
        return super().available and any(g["id"] == self.geofence_id for g in linked)

    @property
    def is_on(self) -> bool | None:
        """Return whether the tracker is inside."""
        if self.position is None:
            return None
        return self.geofence_id in (self.position.get("geofenceIds") or [])
