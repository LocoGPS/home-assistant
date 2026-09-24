"""The LocoGPS integration."""

from __future__ import annotations

import contextlib

from homeassistant.const import CONF_HOST, CONF_TOKEN, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import LocoGPSClient, LocoGPSError
from .const import CONF_SSL
from .coordinator import LocoGPSConfigEntry, LocoGPSCoordinator

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.DEVICE_TRACKER,
    Platform.SENSOR,
]


def _client(hass: HomeAssistant, entry: LocoGPSConfigEntry) -> LocoGPSClient:
    return LocoGPSClient(
        async_get_clientsession(hass),
        entry.data[CONF_HOST],
        token=entry.data[CONF_TOKEN],
        ssl=entry.data.get(CONF_SSL, True),
    )


async def async_setup_entry(hass: HomeAssistant, entry: LocoGPSConfigEntry) -> bool:
    """Set up LocoGPS from a config entry."""
    coordinator = LocoGPSCoordinator(hass, entry, _client(hass, entry))
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    coordinator.async_start_listening()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: LocoGPSConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: LocoGPSConfigEntry) -> None:
    """Revoke the token when the integration is removed."""
    with contextlib.suppress(LocoGPSError):
        await _client(hass, entry).async_revoke_token(entry.data[CONF_TOKEN])
