"""Tests for setup, entities and live updates."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    CONF_HOST,
    CONF_TOKEN,
    STATE_HOME,
    STATE_NOT_HOME,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.locogps.const import CONF_SSL, DOMAIN

from .conftest import setup_entry, wait_for
from .fake_server import HOME_LAT, USER, FakeLocoGPS, position

TRACKER = "device_tracker.edda"
BATTERY = "sensor.edda_battery"
LAST_UPDATE = "sensor.edda_last_update"
HOME_FENCE = "binary_sensor.edda_geofence_zuhause"
ONLINE = "binary_sensor.edda_online"
CHARGING = "binary_sensor.edda_charging"
WIFI_SAVING = "binary_sensor.edda_wlan_power_saving"

# About 250 m north of home, outside the Home Assistant home zone (100 m).
AWAY_LAT = HOME_LAT + 0.00225


async def test_setup_creates_entities(
    hass: HomeAssistant, fake_server: FakeLocoGPS, config_entry: MockConfigEntry
) -> None:
    """All entities show what the server reports."""
    await setup_entry(hass, config_entry, fake_server)
    assert config_entry.state is ConfigEntryState.LOADED

    assert hass.states.get(TRACKER).state == STATE_HOME
    assert hass.states.get(BATTERY).state == "80"
    assert hass.states.get(LAST_UPDATE).state == "2026-09-24T08:00:05+00:00"
    assert hass.states.get(HOME_FENCE).state == STATE_ON
    assert hass.states.get(ONLINE).state == STATE_ON
    assert hass.states.get(CHARGING).state == STATE_OFF
    wifi = hass.states.get(WIFI_SAVING)
    assert wifi.state == STATE_OFF
    assert wifi.attributes["wifi_zone"] is None

    devices = dr.async_entries_for_config_entry(dr.async_get(hass), config_entry.entry_id)
    assert len(devices) == 1
    device = devices[0]
    assert device.identifiers == {(DOMAIN, "1")}
    assert device.name == "Edda"
    assert device.manufacturer == "LocoGPS"
    assert device.model == "LV2"


async def test_live_updates(
    hass: HomeAssistant, fake_server: FakeLocoGPS, config_entry: MockConfigEntry
) -> None:
    """Positions and device changes from the socket show up right away."""
    await setup_entry(hass, config_entry, fake_server)

    await fake_server.push(
        {
            "positions": [
                position(
                    lat=AWAY_LAT,
                    geofence_ids=[],
                    batteryLevel=64,
                    charge=True,
                    fix_time="2026-09-24T08:10:00.000+00:00",
                )
            ]
        }
    )
    await wait_for(hass, lambda: hass.states.get(TRACKER).state == STATE_NOT_HOME)
    assert hass.states.get(HOME_FENCE).state == STATE_OFF
    assert hass.states.get(BATTERY).state == "64"
    assert hass.states.get(CHARGING).state == STATE_ON

    device = {**fake_server.devices[0], "status": "offline"}
    await fake_server.push({"devices": [device]})
    await wait_for(hass, lambda: hass.states.get(ONLINE).state == STATE_OFF)


async def test_stale_fix_does_not_move_tracker(
    hass: HomeAssistant, fake_server: FakeLocoGPS, config_entry: MockConfigEntry
) -> None:
    """An invalid fix repeats an old position and must not end the stay at home.

    This is the case behind the phantom geofence exits. The server keeps the
    geofence state, the integration keeps the location.
    """
    await setup_entry(hass, config_entry, fake_server)

    await fake_server.push(
        {
            "positions": [
                position(
                    lat=AWAY_LAT,
                    valid=False,
                    geofence_ids=[10],
                    batteryLevel=79,
                    fix_time="2026-09-24T08:10:00.000+00:00",
                )
            ]
        }
    )
    await wait_for(hass, lambda: hass.states.get(BATTERY).state == "79")
    tracker = hass.states.get(TRACKER)
    assert tracker.state == STATE_HOME
    assert tracker.attributes["latitude"] == HOME_LAT
    assert hass.states.get(HOME_FENCE).state == STATE_ON

    # A WLAN position is approximate but real, it moves the tracker.
    await fake_server.push(
        {
            "positions": [
                position(
                    lat=AWAY_LAT,
                    valid=False,
                    approximate=True,
                    geofence_ids=[],
                    fix_time="2026-09-24T08:15:00.000+00:00",
                )
            ]
        }
    )
    await wait_for(hass, lambda: hass.states.get(TRACKER).state == STATE_NOT_HOME)


async def test_older_position_is_ignored(
    hass: HomeAssistant, fake_server: FakeLocoGPS, config_entry: MockConfigEntry
) -> None:
    """A position older than the current one does not replace it."""
    await setup_entry(hass, config_entry, fake_server)
    await fake_server.push(
        {"positions": [position(lat=AWAY_LAT, fix_time="2026-09-24T07:00:00.000+00:00")]}
    )
    await fake_server.push(
        {"positions": [position(batteryLevel=55, fix_time="2026-09-24T08:20:00.000+00:00")]}
    )
    await wait_for(hass, lambda: hass.states.get(BATTERY).state == "55")
    assert hass.states.get(TRACKER).state == STATE_HOME


async def test_wifi_energy_saving(
    hass: HomeAssistant, fake_server: FakeLocoGPS, config_entry: MockConfigEntry
) -> None:
    """The WLAN power saving mode follows the tracker's own report."""
    await setup_entry(hass, config_entry, fake_server)
    await fake_server.push(
        {"positions": [position(homeStatus=49, fix_time="2026-09-24T08:30:00.000+00:00")]}
    )
    await wait_for(hass, lambda: hass.states.get(WIFI_SAVING).state == STATE_ON)
    assert hass.states.get(WIFI_SAVING).attributes["wifi_zone"] == "FRITZ!Box 7590"


async def test_new_geofence_and_device(
    hass: HomeAssistant, fake_server: FakeLocoGPS, config_entry: MockConfigEntry
) -> None:
    """Geofences and devices added later get their entities on the next refresh."""
    await setup_entry(hass, config_entry, fake_server)
    fake_server.geofences[1].append({"id": 11, "name": "Hundeschule"})
    fake_server.devices.append({**fake_server.devices[0], "id": 2, "name": "Balu"})
    fake_server.positions.append(position(device_id=2, geofence_ids=[]))

    await config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.edda_geofence_hundeschule").state == STATE_OFF
    assert hass.states.get("device_tracker.balu").state == STATE_HOME
    assert hass.states.get("sensor.balu_battery").state == "80"

    # A geofence that is unlinked from the device becomes unavailable.
    fake_server.geofences[1] = [{"id": 10, "name": "Zuhause"}]
    await config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.edda_geofence_hundeschule").state == "unavailable"


async def test_socket_reconnects(
    hass: HomeAssistant, fake_server: FakeLocoGPS, config_entry: MockConfigEntry
) -> None:
    """A dropped live connection is opened again."""
    await setup_entry(hass, config_entry, fake_server)
    await fake_server.close_sockets()
    await wait_for(hass, lambda: len(fake_server.socket_tokens) == 2)
    await wait_for(hass, lambda: fake_server.socket_count == 1)


async def test_revoked_token_starts_reauth(
    hass: HomeAssistant, fake_server: FakeLocoGPS, config_entry: MockConfigEntry
) -> None:
    """Without a valid token the user is asked to log in again."""
    fake_server.revoked.add(config_entry.data[CONF_TOKEN])
    config_entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]


async def test_firewall_html_is_an_error(
    hass: HomeAssistant, fake_server: FakeLocoGPS, config_entry: MockConfigEntry
) -> None:
    """An HTML page instead of JSON makes the setup retry later."""
    fake_server.html_firewall = True
    config_entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_token_is_renewed(hass: HomeAssistant, fake_server: FakeLocoGPS) -> None:
    """A token with less than a month left is replaced by a fresh one."""
    old_token = fake_server.issue_token(datetime.now(UTC) + timedelta(days=10))
    entry = _entry(fake_server, old_token)
    await setup_entry(hass, entry, fake_server)

    new_token = entry.data[CONF_TOKEN]
    assert new_token != old_token
    assert new_token in fake_server.valid_tokens()
    assert old_token in fake_server.revoked
    assert fake_server.tokens[new_token] - datetime.now(UTC) > timedelta(days=364)

    # The live socket reconnects with the new token.
    await fake_server.close_sockets()
    await wait_for(hass, lambda: fake_server.socket_tokens[-1] == new_token)


@pytest.mark.parametrize(("days_left", "reauth"), [(20, False), (3, True)])
async def test_token_not_extended(
    hass: HomeAssistant, fake_server: FakeLocoGPS, days_left: int, reauth: bool
) -> None:
    """If the server refuses a longer lifetime, the user is asked a week ahead."""
    fake_server.cap_bearer_tokens = True
    old_token = fake_server.issue_token(datetime.now(UTC) + timedelta(days=days_left))
    entry = _entry(fake_server, old_token)
    await setup_entry(hass, entry, fake_server)

    assert entry.state is ConfigEntryState.LOADED
    assert entry.data[CONF_TOKEN] == old_token
    assert fake_server.valid_tokens() == {old_token}
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert bool(flows) is reauth


async def test_remove_revokes_token(
    hass: HomeAssistant, fake_server: FakeLocoGPS, config_entry: MockConfigEntry
) -> None:
    """Removing the integration revokes its token on the server."""
    await setup_entry(hass, config_entry, fake_server)
    token = config_entry.data[CONF_TOKEN]
    assert await hass.config_entries.async_remove(config_entry.entry_id)
    await hass.async_block_till_done()
    assert token in fake_server.revoked


async def test_unload(
    hass: HomeAssistant, fake_server: FakeLocoGPS, config_entry: MockConfigEntry
) -> None:
    """Unloading closes the live socket."""
    await setup_entry(hass, config_entry, fake_server)
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.NOT_LOADED
    await wait_for(hass, lambda: fake_server.socket_count == 0)


def _entry(fake_server: FakeLocoGPS, token: str) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=str(USER["id"]),
        title=USER["email"],
        data={CONF_HOST: fake_server.host, CONF_SSL: False, CONF_TOKEN: token},
    )


async def test_german_entity_ids(
    hass: HomeAssistant, fake_server: FakeLocoGPS, config_entry: MockConfigEntry
) -> None:
    """A German installation gets the entity ids used in the README."""
    hass.config.language = "de"
    await setup_entry(hass, config_entry, fake_server)
    for entity_id in (
        "device_tracker.edda",
        "binary_sensor.edda_geozaun_zuhause",
        "sensor.edda_akku",
        "binary_sensor.edda_laden",
        "binary_sensor.edda_online",
        "sensor.edda_letzte_aktualisierung",
        "binary_sensor.edda_wlan_energiesparmodus",
    ):
        assert hass.states.get(entity_id) is not None, entity_id
