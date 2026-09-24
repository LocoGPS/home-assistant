"""Fixtures for the LocoGPS tests."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator, Callable

import pytest
import pytest_socket
from homeassistant.const import CONF_HOST, CONF_TOKEN
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.locogps.const import CONF_SSL, DOMAIN

from .fake_server import USER, FakeLocoGPS

if sys.platform == "win32":
    # The harness forbids new sockets, but asyncio on Windows needs a socket
    # pair for every event loop. Connections stay limited to 127.0.0.1.
    pytest_socket.disable_socket = lambda *args, **kwargs: None


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load the integration from custom_components."""


@pytest.fixture(autouse=True)
def fast_reconnect(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reconnect the live socket without the production delay."""
    monkeypatch.setattr("custom_components.locogps.coordinator.SOCKET_RECONNECT_MIN", 0.05)


@pytest.fixture
async def fake_server() -> AsyncIterator[FakeLocoGPS]:
    """Run a fake LocoGPS server."""
    server = FakeLocoGPS()
    await server.start()
    yield server
    await server.close()


@pytest.fixture
def config_entry(fake_server: FakeLocoGPS) -> MockConfigEntry:
    """Return an entry with a token that is valid for 200 more days."""
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=str(USER["id"]),
        title=USER["email"],
        data={CONF_HOST: fake_server.host, CONF_SSL: False, CONF_TOKEN: fake_server.issue_token()},
    )


async def wait_for(hass: HomeAssistant, condition: Callable[[], bool], timeout: float = 3) -> None:
    """Wait until the condition holds, the live socket runs in the background."""
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.01)
            await hass.async_block_till_done()


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry, server: FakeLocoGPS) -> None:
    """Set up the entry and wait for the live socket."""
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await wait_for(hass, lambda: server.socket_count == 1)
