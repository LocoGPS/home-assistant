"""Sensors of LocoGPS trackers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.util import dt as dt_util

from .coordinator import LocoGPSConfigEntry, LocoGPSCoordinator
from .entity import LocoGPSEntity, add_entities_when_new

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class LocoGPSSensorDescription(SensorEntityDescription):
    """Describes a LocoGPS sensor."""

    value_fn: Callable[[LocoGPSEntity], StateType | datetime]


def _battery(entity: LocoGPSEntity) -> StateType:
    value = entity.position_attributes.get("batteryLevel")
    return int(value) if isinstance(value, int | float) else None


def _last_update(entity: LocoGPSEntity) -> datetime | None:
    value = (entity.device or {}).get("lastUpdate")
    return dt_util.parse_datetime(value) if value else None


SENSORS: tuple[LocoGPSSensorDescription, ...] = (
    LocoGPSSensorDescription(
        key="battery",
        translation_key="battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_battery,
    ),
    LocoGPSSensorDescription(
        key="last_update",
        translation_key="last_update",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_last_update,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LocoGPSConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors of every device."""

    def build(coordinator: LocoGPSCoordinator):
        for device_id in coordinator.data.devices:
            for description in SENSORS:
                yield (
                    f"{device_id}_{description.key}",
                    lambda device_id=device_id, description=description: LocoGPSSensor(
                        coordinator, device_id, description
                    ),
                )

    add_entities_when_new(entry, async_add_entities, build)


class LocoGPSSensor(LocoGPSEntity, SensorEntity):
    """A value reported by the tracker."""

    entity_description: LocoGPSSensorDescription

    def __init__(
        self,
        coordinator: LocoGPSCoordinator,
        device_id: int,
        description: LocoGPSSensorDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device_id, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> StateType | datetime:
        """Return the value."""
        return self.entity_description.value_fn(self)
