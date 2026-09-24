"""Coordinator that keeps the LocoGPS data of one account up to date."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_TOKEN
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import LocoGPSAuthError, LocoGPSClient, LocoGPSError, token_expiration
from .const import (
    DOMAIN,
    SOCKET_RECONNECT_MAX,
    SOCKET_RECONNECT_MIN,
    TOKEN_LIFETIME,
    TOKEN_REAUTH_BEFORE,
    TOKEN_RENEW_BEFORE,
    UPDATE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

type LocoGPSConfigEntry = ConfigEntry[LocoGPSCoordinator]


@dataclass
class LocoGPSData:
    """Everything the entities read, indexed by device id."""

    devices: dict[int, dict[str, Any]] = field(default_factory=dict)
    # Latest position as reported, used for battery, geofences and status.
    positions: dict[int, dict[str, Any]] = field(default_factory=dict)
    # Latest position with a trustworthy location, used for the map.
    locations: dict[int, dict[str, Any]] = field(default_factory=dict)
    # Geofences linked to each device.
    geofences: dict[int, list[dict[str, Any]]] = field(default_factory=dict)


def has_usable_location(position: dict[str, Any]) -> bool:
    """Tell whether a position may move the tracker.

    Same rule as the geofence handler on the server. An invalid fix without
    the approximate flag repeats an old GPS position, which an LV2 sends after
    an empty WLAN scan or while it has no satellites. It can lie a few hundred
    meters off and would make the tracker leave home in Home Assistant.
    """
    if position.get("valid"):
        return True
    return bool((position.get("attributes") or {}).get("approximate"))


def _is_older(position: dict[str, Any], current: dict[str, Any] | None) -> bool:
    if current is None:
        return False
    new_time = dt_util.parse_datetime(str(position.get("fixTime") or ""))
    old_time = dt_util.parse_datetime(str(current.get("fixTime") or ""))
    if new_time is None or old_time is None:
        return False
    return new_time < old_time


class LocoGPSCoordinator(DataUpdateCoordinator[LocoGPSData]):
    """Poll the account every few minutes and apply live updates in between."""

    config_entry: LocoGPSConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: LocoGPSConfigEntry, client: LocoGPSClient
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self.client = client
        self._reauth_started = False

    async def _async_update_data(self) -> LocoGPSData:
        try:
            await self._async_check_token()
            devices = await self.client.async_get_devices()
            positions = await self.client.async_get_positions()
            geofences = {
                device["id"]: await self.client.async_get_geofences(device["id"])
                for device in devices
            }
        except LocoGPSAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="auth_failed"
            ) from err
        except LocoGPSError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
                translation_placeholders={"error": str(err)},
            ) from err

        # Keep what the socket delivered, the refresh may return an older state.
        previous = self.data or LocoGPSData()
        data = LocoGPSData(
            devices={device["id"]: device for device in devices},
            geofences=geofences,
        )
        for device_id in data.devices:
            if device_id in previous.positions:
                data.positions[device_id] = previous.positions[device_id]
            if device_id in previous.locations:
                data.locations[device_id] = previous.locations[device_id]
        for position in positions:
            self._apply_position(data, position)
        return data

    @staticmethod
    def _apply_position(data: LocoGPSData, position: dict[str, Any]) -> bool:
        """Store a position and return whether anything changed."""
        device_id = position.get("deviceId")
        if device_id not in data.devices:
            return False
        if _is_older(position, data.positions.get(device_id)):
            return False
        data.positions[device_id] = position
        # Without any earlier location an old fix is still better than none.
        if has_usable_location(position) or device_id not in data.locations:
            data.locations[device_id] = position
        return True

    async def _async_check_token(self) -> None:
        """Renew the token in time, or ask for the password before it expires."""
        expiration = token_expiration(self.client.token)
        if expiration is None:
            return
        now = dt_util.utcnow()
        if expiration - now > TOKEN_RENEW_BEFORE:
            return

        old_token = self.client.token
        try:
            new_token = await self.client.async_create_token(now + TOKEN_LIFETIME)
        except LocoGPSAuthError:
            raise
        except LocoGPSError as err:
            _LOGGER.warning("Could not renew the LocoGPS token: %s", err)
            return

        new_expiration = token_expiration(new_token)
        if new_expiration is None or new_expiration <= expiration:
            # The server did not extend the lifetime. Drop the new token and
            # ask for the password while the old one still works.
            await self._async_revoke(new_token)
            if expiration - now < TOKEN_REAUTH_BEFORE and not self._reauth_started:
                self._reauth_started = True
                self.config_entry.async_start_reauth(self.hass)
            return

        self.client.token = new_token
        self.hass.config_entries.async_update_entry(
            self.config_entry, data={**self.config_entry.data, CONF_TOKEN: new_token}
        )
        _LOGGER.debug("LocoGPS token renewed until %s", new_expiration)
        await self._async_revoke(old_token)

    async def _async_revoke(self, token: str | None) -> None:
        if not token:
            return
        try:
            await self.client.async_revoke_token(token)
        except LocoGPSError as err:
            _LOGGER.debug("Could not revoke token: %s", err)

    @callback
    def async_start_listening(self) -> None:
        """Keep the live socket open for as long as the entry is loaded."""
        self.config_entry.async_create_background_task(
            self.hass, self._async_listen_forever(), name=f"{DOMAIN} live socket"
        )

    async def _async_listen_forever(self) -> None:
        delay = SOCKET_RECONNECT_MIN
        while True:
            started = time.monotonic()
            try:
                await self.client.async_listen(self._handle_message)
            except LocoGPSError as err:
                _LOGGER.debug("LocoGPS live connection lost: %s", err)
            else:
                _LOGGER.debug("LocoGPS live connection closed")
            if time.monotonic() - started > 60:
                delay = SOCKET_RECONNECT_MIN
            await asyncio.sleep(delay)
            delay = min(delay * 2, SOCKET_RECONNECT_MAX)

    @callback
    def _handle_message(self, message: dict[str, Any]) -> None:
        """Apply a live update without waiting for the next refresh."""
        if self.data is None:
            return
        changed = False
        unknown_device = False
        for device in message.get("devices") or []:
            if device.get("id") in self.data.devices:
                self.data.devices[device["id"]] = device
                changed = True
            else:
                unknown_device = True
        for position in message.get("positions") or []:
            if position.get("deviceId") not in self.data.devices:
                unknown_device = True
            elif self._apply_position(self.data, position):
                changed = True
        if changed:
            # Notify the entities without resetting the refresh timer, so the
            # periodic refresh still runs while positions keep coming in.
            self.async_update_listeners()
        if unknown_device:
            self.config_entry.async_create_task(self.hass, self.async_request_refresh())
