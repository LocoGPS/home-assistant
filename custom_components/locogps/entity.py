"""Base entity for LocoGPS."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONFIGURATION_URL, DOMAIN
from .coordinator import LocoGPSConfigEntry, LocoGPSCoordinator


class LocoGPSEntity(CoordinatorEntity[LocoGPSCoordinator]):
    """An entity that belongs to one tracker."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: LocoGPSCoordinator, device_id: int, key: str) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self.device_id = device_id
        device = coordinator.data.devices[device_id]
        self._attr_unique_id = f"{device_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(device_id))},
            manufacturer="LocoGPS",
            model=device.get("model") or None,
            name=device.get("name"),
            configuration_url=CONFIGURATION_URL,
        )

    @property
    def device(self) -> dict[str, Any] | None:
        """Return the device as reported by the server."""
        return self.coordinator.data.devices.get(self.device_id)

    @property
    def position(self) -> dict[str, Any] | None:
        """Return the latest position of the device."""
        return self.coordinator.data.positions.get(self.device_id)

    @property
    def position_attributes(self) -> dict[str, Any]:
        """Return the attributes of the latest position."""
        return (self.position or {}).get("attributes") or {}

    @property
    def available(self) -> bool:
        """Return whether the device still belongs to the account."""
        return super().available and self.device is not None


def add_entities_when_new(
    entry: LocoGPSConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    build: Callable[[LocoGPSCoordinator], Iterable[tuple[str, Callable[[], Entity]]]],
) -> None:
    """Add entities now and whenever a new device or geofence shows up.

    build yields a unique key and a factory for every entity that should
    exist. Factories run only for keys that were not added before.
    """
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _add_new() -> None:
        new_entities = []
        for key, factory in build(coordinator):
            if key not in known:
                known.add(key)
                new_entities.append(factory())
        if new_entities:
            async_add_entities(new_entities)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))
